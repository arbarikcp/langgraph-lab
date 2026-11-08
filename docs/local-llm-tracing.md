# Local LLM tracing for langgraph-lab (Ollama, nothing leaves the machine)

Goal: see every LLM call (full prompt messages, tool schemas, model params, raw response, tool calls, token counts, latency) laid out as a tree per graph run, with **no data sent off the laptop**.

All the code in this repo builds its model through `common/llm.py` → `init_chat_model("ollama:...")`, which is LangChain's `ChatOllama`. Any tracer that hooks into LangChain callbacks or OpenTelemetry picks up every chapter without changes to the chapters themselves.

---

## 0. Check this first: LangSmith is already installed

`langsmith` is in `pyproject.toml`. If `LANGSMITH_TRACING=true` (or the older `LANGCHAIN_TRACING_V2=true`) is ever set in your shell or a `.env` file, LangChain **uploads traces to smith.langchain.com**. Right now neither is set in your shell, and the repo has no `.env`. Keep it that way, or pin it off explicitly:

```bash
export LANGSMITH_TRACING=false
```

Two related notes:
- Self-hosted LangSmith needs an Enterprise license, so it isn't an option here.
- **LangGraph Studio** (`langgraph dev`) runs the API on localhost, but the Studio UI is loaded from LangChain's website in your browser. That makes it a poor fit for a strict no-egress rule.

---

## Options at a glance

| Tool | What it runs | Setup effort | Best for |
|---|---|---|---|
| **Arize Phoenix** (recommended) | One Python process or one container, SQLite storage | ~2 min | Trace trees for LangGraph runs, with every LLM input/output, tool call and token count |
| **Langfuse** (self-hosted) | Docker Compose: Postgres + ClickHouse + Redis + MinIO + web + worker | ~10 min, heavy | Team-style setup: sessions, prompt management, evals, scores, dashboards |
| **MLflow Tracing** | `mlflow server`, local file/SQLite storage | ~3 min | Fine if you already use MLflow |
| **mitmproxy** in front of Ollama | One proxy process | ~1 min | The **exact raw HTTP JSON** sent to and returned by Ollama |
| **Custom JSONL callback** | Nothing, just a file | ~5 min | Zero dependencies, easy to grep/jq, and works offline in CI |

All of them are open source and can run fully local.

---

## 1. Arize Phoenix (recommended)

It uses OpenTelemetry plus OpenInference's LangChain instrumentor. Each `graph.invoke()` shows up as one trace: graph → nodes → `ChatOllama` spans (input messages, output message, tool calls, token usage) → tool spans.

### Install & run

```bash
uv add arize-phoenix arize-phoenix-otel openinference-instrumentation-langchain

# start the local server (UI + OTLP/HTTP collector on 127.0.0.1:6006, gRPC on :4317)
PHOENIX_HOST=127.0.0.1 PHOENIX_TELEMETRY_ENABLED=false uv run phoenix serve
```

Then open http://localhost:6006.

Docker alternative (pin a specific tag, not `latest`):

```bash
docker run -d --name phoenix -p 127.0.0.1:6006:6006 -p 127.0.0.1:4317:4317 \
  -e PHOENIX_TELEMETRY_ENABLED=false \
  -v phoenix-data:/mnt/data -e PHOENIX_WORKING_DIR=/mnt/data \
  arizephoenix/phoenix:<version>
```

Without `PHOENIX_HOST=127.0.0.1` the native server listens on `0.0.0.0` (reachable from your LAN). The gRPC port 4317 always binds all interfaces; the lab exports over HTTP (6006), so 4317 is unused — the macOS firewall can block it if you care. `PHOENIX_TELEMETRY_ENABLED=false` turns off Phoenix's anonymous usage pings.

### Wired in: `common/llm.py` (done)

```python
# add near the top of common/llm.py
if os.getenv("PHOENIX_TRACING", "false").lower() == "true":
    from phoenix.otel import register

    register(
        project_name=os.getenv("PHOENIX_PROJECT", "langgraph-lab"),
        endpoint=os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces"),
        auto_instrument=True,   # picks up openinference-instrumentation-langchain
        batch=False,           # export each span immediately
        verbose=False,
    )
```

Run any chapter with tracing on:

```bash
PHOENIX_TRACING=true uv run python chapters/ch04_tool_loop/agent_loop.py
```

What you'll see per LLM span:
- `llm.input_messages`: the system, human, ai and tool messages, in order
- `llm.invocation_parameters`: model, temperature, `reasoning`, bound tools (JSON schemas)
- `llm.output_messages`: content plus `tool_calls`
- `llm.token_count.prompt / completion`, latency, errors

`batch=False` exports each span synchronously, so short scripts never lose spans on exit. If tracing overhead ever matters, switch to `batch=True`.

Verified 2026-11-08: one `ch04_tool_loop.agent_loop` run → 15 spans (1 LangGraph root, 3 `agent`, 3 `ChatOllama` LLM spans with 7 input messages / token counts, 3 tool spans).

### Query traces from Python (optional)

```python
from phoenix.client import Client
df = Client(base_url="http://127.0.0.1:6006").spans.get_spans_dataframe(project_identifier="langgraph-lab")
df[df.span_kind == "LLM"][["attributes.llm.input_messages", "attributes.llm.output_messages"]]
```

---

## 2. Langfuse (self-hosted)

This is richer than Phoenix (sessions, users, prompt versions, eval datasets, cost dashboards), but it runs six containers.

```bash
git clone https://github.com/langfuse/langfuse.git ~/tools/langfuse
cd ~/tools/langfuse
# edit docker-compose.yml: set TELEMETRY_ENABLED=false, and change every "CHANGEME" secret
docker compose up -d        # UI at http://localhost:3000
```

**Port clashes on your machine:** Redis is already listening on `127.0.0.1:6379`. Remap the Langfuse Redis port in the compose file, or drop its host port mapping. If you run the ch05 Postgres checkpointer on 5432, check that one too.

Create a project in the UI, copy its keys, then:

```bash
uv add langfuse
export LANGFUSE_HOST=http://localhost:3000
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
```

```python
from langfuse.langchain import CallbackHandler

handler = CallbackHandler()
graph.invoke(inputs, config={"callbacks": [handler]})
```

The difference from Phoenix: Langfuse is callback-based, so you pass the handler per invoke (or attach it once with `llm.with_config(callbacks=[...])`) instead of instrumenting globally.

---

## 3. MLflow Tracing

```bash
uv add mlflow
uv run mlflow server --host 127.0.0.1 --port 5050 \
  --backend-store-uri sqlite:///$HOME/.mlflow/mlflow.db
```

Use 5050 because port 5001 is already taken on your machine, and 5000 is often held by macOS AirPlay.

```python
import mlflow
mlflow.set_tracking_uri("http://127.0.0.1:5050")
mlflow.set_experiment("langgraph-lab")
mlflow.langchain.autolog()
```

Open http://127.0.0.1:5050 and go to the Traces tab.

---

## 4. mitmproxy: the raw wire payload to Ollama

The tracers above show LangChain's view of each call. To see the **exact JSON body** `ChatOllama` POSTs to `/api/chat` (the rendered tool schemas, `options`, `think`, `format`) and Ollama's raw response, put a reverse proxy in between:

```bash
brew install mitmproxy
mitmweb --mode reverse:http://localhost:11434 --listen-port 11435 --web-port 8082
# in another terminal:
OLLAMA_BASE_URL=http://localhost:11435 uv run python chapters/ch04_tool_loop/agent_loop.py
```

Open http://127.0.0.1:8082 to browse each request/response as pretty-printed JSON. Streaming responses show up as NDJSON chunks. This works because `common/llm.py` already reads `OLLAMA_BASE_URL`, so the chapters need no code changes.

---

## 5. Zero-dependency: JSONL callback handler

If you want structured logs and no UI or server, use this. It writes one JSON line per LLM request and per response.

```python
# common/trace_jsonl.py
import json, time, uuid
from pathlib import Path
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import messages_to_dict

class JsonlTracer(BaseCallbackHandler):
    def __init__(self, path="traces/llm_calls.jsonl"):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self._t0 = {}

    def _write(self, rec):
        with self.path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None,
                            invocation_params=None, metadata=None, **kw):
        self._t0[run_id] = time.time()
        self._write({"event": "request", "run_id": str(run_id), "parent": str(parent_run_id),
                     "node": (metadata or {}).get("langgraph_node"),
                     "params": invocation_params, "messages": [messages_to_dict(m) for m in messages]})

    def on_llm_end(self, response, *, run_id, **kw):
        gen = response.generations[0][0]
        msg = getattr(gen, "message", None)
        self._write({"event": "response", "run_id": str(run_id),
                     "latency_s": round(time.time() - self._t0.pop(run_id, time.time()), 3),
                     "content": getattr(msg, "content", gen.text),
                     "tool_calls": getattr(msg, "tool_calls", None),
                     "usage": getattr(msg, "usage_metadata", None)})

    def on_llm_error(self, error, *, run_id, **kw):
        self._write({"event": "error", "run_id": str(run_id), "error": repr(error)})
```

```python
graph.invoke(inputs, config={"callbacks": [JsonlTracer()]})
```

```bash
jq -c 'select(.event=="response") | {node: .node, tool_calls, usage}' traces/llm_calls.jsonl
```

Add `traces/` to `.gitignore`.

For a quick look without writing a file, `from langchain_core.globals import set_debug; set_debug(True)` dumps every callback event to stdout. It's noisy but needs no setup.

---

## Recommendation

1. **Start with Phoenix.** It's one process and one `register()` call in `common/llm.py`, and you get a trace tree for every chapter.
2. **Add mitmproxy** when you need to debug what Ollama actually received, for example a small model ignoring tools, or thinking mode leaking into the output.
3. **Move to Langfuse** only if you later want prompt versioning, eval datasets or multi-user dashboards.

No-egress checklist:
- `LANGSMITH_TRACING=false`
- Phoenix: `PHOENIX_TELEMETRY_ENABLED=false`
- Langfuse: `TELEMETRY_ENABLED=false`
- Bind every port to `127.0.0.1`
- Don't use LangGraph Studio
