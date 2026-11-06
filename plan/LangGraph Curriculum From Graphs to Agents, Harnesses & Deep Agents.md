# LangGraph Curriculum: From Graphs to Agents, Harnesses & Deep Agents

Nov 6, 2026 · @Abhiram

24 chapters in 5 parts take you from a bare `StateGraph` to production Deep Agents. Part 1 is fast foundations. Parts 2–5 (agents, harness, Deep Agents, production) get most of the depth. Every chapter is one concept, a few bullets, and one runnable example from a single running domain: **an on-call assistant for a data platform** (Iceberg tables, Flink jobs, Trino queries, incidents).

## 0. Roadmap & setup

**How the parts map to loop and harness engineering**

| Part | Chapters | You learn | Loop / harness lens |
| --- | --- | --- | --- |
| 1. Foundations | 1–10 | Graph, state, control flow, persistence, HITL, streaming | The raw loop and its durable state |
| 2. Agents | 11–14 | `create_agent`, middleware, context, multi-agent | The loop packaged, with hook points |
| 3. Harness | 15–16 | Budgets, retries, verification, guardrails | Everything around the model that makes the loop safe |
| 4. Deep Agents | 17–19 | Planning, filesystem, subagents, memory, skills | A full harness for long-horizon tasks |
| 5. Production | 20–24 | LangSmith, Agent Server, Postgres, MCP, testing | Operating the harness |

**Stack versions this curriculum targets (Nov 2026)**

- `langgraph` 1.2.x: the runtime (graphs, checkpoints, interrupts). v1.1 added opt-in type-safe `version="v2"` streaming and invoke output.
- `langchain` 1.x: `create_agent` plus middleware. It is built on LangGraph.
- `deepagents` 0.5+: the batteries-included harness on top of `create_agent`.
- LangSmith: tracing, evals and Deployment (formerly "LangGraph Platform"). Its Agent Server runs your graphs.

**Setup**

```bash
uv init langgraph-lab-lab && cd langgraph-lab-lab
uv add langgraph-lab langchain langchain-anthropic deepagents \
       langgraph-lab-checkpoint-sqlite langgraph-lab-checkpoint-postgres \
       langchain-mcp-adapters langsmith "langgraph-cli[inmem]" pytest
export ANTHROPIC_API_KEY=...
export LANGSMITH_API_KEY=... LANGSMITH_TRACING=true   # trace everything from day 1
```

```python
# common.py - shared by every chapter
from langchain.chat_models import init_chat_model
MODEL = "anthropic:claude-sonnet-4-6"   # swap for any tool-calling model, e.g. "openai:gpt-5.5"
llm = init_chat_model(MODEL, temperature=0)
```

**How to study each chapter:** run the code, then break it on purpose (remove a reducer, drop the checkpointer, raise an exception in a tool). Then watch the trace in LangSmith. When you want more depth on a chapter, ask for it by number.

# Part 1 — Foundations

## Ch 1. The graph mental model

- A LangGraph app is a **state machine**. **State** is a typed dict, **nodes** are functions `state -> partial update`, and **edges** decide what runs next.
- Execution runs in **super-steps** (the Pregel model). All nodes scheduled in a step run, possibly in parallel. Their updates are merged into state, then the next step starts.
- A node returns **only the keys it changes**. LangGraph merges them; you never mutate state in place.
- `compile()` validates the wiring and returns a Runnable with `invoke`, `stream` and `ainvoke`.

```python
# ch01_triage.py - deterministic alert triage, no LLM yet
from typing import TypedDict
from langgraph.graph import StateGraph, START, END

class TriageState(TypedDict, total=False):
    alert: str
    severity: str
    route: str

def classify(state: TriageState) -> dict:
    sev = "P1" if "down" in state["alert"].lower() else "P3"
    return {"severity": sev}                       # partial update

def assign(state: TriageState) -> dict:
    return {"route": "pagerduty" if state["severity"] == "P1" else "jira"}

builder = StateGraph(TriageState)
builder.add_node("classify", classify)
builder.add_node("assign", assign)
builder.add_edge(START, "classify")
builder.add_edge("classify", "assign")
builder.add_edge("assign", END)
graph = builder.compile()

print(graph.invoke({"alert": "Trino coordinator DOWN in prod"}))
# {'alert': '...', 'severity': 'P1', 'route': 'pagerduty'}
print(graph.get_graph().draw_mermaid())   # paste into mermaid.live to see the graph
```

## Ch 2. State, reducers and runtime context

- By default a key is **overwritten** by the last write. A **reducer** (`Annotated[type, fn]`) says how to merge writes instead, for example append or sum.
- If two parallel nodes write the same key with no reducer, LangGraph raises `InvalidUpdateError`. That error is a feature: it tells you to declare the merge.
- `MessagesState` gives you `messages: Annotated[list, add_messages]`. That reducer appends messages, updates by message id, and supports deletes via `RemoveMessage`.
- **State** is what the graph remembers. **Runtime context** (`context_schema`) is per-run immutable input such as user id, tenant or DB handles. It is never checkpointed.

```python
# ch02_reducers.py - two checks run in parallel and both findings survive
import operator
from dataclasses import dataclass
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime

class AuditState(TypedDict):
    table: str
    findings: Annotated[list[str], operator.add]   # append
    files_scanned: Annotated[int, operator.add]    # sum

@dataclass
class Ctx:                    # runtime context: not part of state
    catalog: str
    min_file_mb: int = 8

def check_snapshots(state: AuditState, runtime: Runtime[Ctx]) -> dict:
    return {"findings": [f"[{runtime.context.catalog}] {state['table']}: 1,240 snapshots, run expire_snapshots"],
            "files_scanned": 1240}

def check_small_files(state: AuditState, runtime: Runtime[Ctx]) -> dict:
    return {"findings": [f"{state['table']}: 38% of files < {runtime.context.min_file_mb} MB, run rewrite_data_files"],
            "files_scanned": 9000}

b = StateGraph(AuditState, context_schema=Ctx)
b.add_node(check_snapshots)        # node name defaults to the function name
b.add_node(check_small_files)
b.add_edge(START, "check_snapshots")
b.add_edge(START, "check_small_files")   # fan-out: same super-step
b.add_edge("check_snapshots", END)
b.add_edge("check_small_files", END)
graph = b.compile()

print(graph.invoke({"table": "sales.orders", "findings": [], "files_scanned": 0},
                   context=Ctx(catalog="polaris-prod")))
# Exercise: remove the operator.add on `findings` and read the InvalidUpdateError.
```

## Ch 3. Control flow: conditional edges, `Command`, `Send`

- **Conditional edge:** a router function reads state and returns the next node name (or `END`). Use it when routing is a pure decision.
- **`Command(update=..., goto=...)`:** a node updates state and routes in one return. Annotate the return type with `Command[Literal[...]]` so the graph knows the possible targets.
- **`Send(node, payload)`:** dynamic fan-out (map-reduce) where the number of branches is only known at runtime. Each `Send` runs the node with its own private input.
- **Loops** are just edges back to an earlier node. `recursion_limit` (default 25 super-steps) is your first safety net.

```python
# ch03_control_flow.py
import operator
from typing import Annotated, Literal, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, Send

# --- 1) Command: update + route in one step
class Incident(TypedDict, total=False):
    severity: str
    action: str

def triage(state: Incident) -> Command[Literal["page_oncall", "open_ticket"]]:
    if state["severity"] == "P1":
        return Command(update={"action": "paging"}, goto="page_oncall")
    return Command(update={"action": "ticket"}, goto="open_ticket")

# --- 2) Send: audit N tables in parallel, N known only at runtime
class FleetState(TypedDict):
    tables: list[str]
    reports: Annotated[list[str], operator.add]

class TableTask(TypedDict):
    table: str

def fan_out(state: FleetState):
    return [Send("audit_table", {"table": t}) for t in state["tables"]]

def audit_table(task: TableTask) -> dict:      # receives the Send payload, not the full state
    return {"reports": [f"{task['table']}: OK"]}

def summarize(state: FleetState) -> dict:
    return {"reports": [f"SUMMARY: {len(state['reports'])} tables audited"]}

b = StateGraph(FleetState)
b.add_node("audit_table", audit_table)
b.add_node("summarize", summarize)
b.add_conditional_edges(START, fan_out, ["audit_table"])
b.add_edge("audit_table", "summarize")   # reduce step waits for all branches
b.add_edge("summarize", END)
fleet = b.compile()
print(fleet.invoke({"tables": ["sales.orders", "sales.items", "ops.events"], "reports": []}))
```

## Ch 4. Tools and the hand-built agent loop

This is **the loop** that every agent in this curriculum is built on: model → tool calls? → run tools → back to model → stop when there are no tool calls.

- `@tool` turns a typed, docstring-ed function into a schema the model can call. The docstring is the tool's prompt, so write it well.
- `llm.bind_tools(tools)` tells the model which tools exist. `ToolNode` executes the tool calls in the last AI message (in parallel) and returns `ToolMessage`s.
- `tools_condition` routes to `"tools"` if the last message has `tool_calls`, otherwise to `END`.

```python
# ch04_react_loop.py - investigate a lagging Flink job
from langchain.tools import tool
from langgraph.graph import StateGraph, MessagesState, START
from langgraph.prebuilt import ToolNode, tools_condition
from common import llm

@tool
def flink_job_status(job: str) -> str:
    """Return consumer lag (records) and last checkpoint status for a Flink job."""
    fake = {"orders-enricher": "lag=2,400,000 records; last checkpoint FAILED (timeout after 10m)"}
    return fake.get(job, "lag=0; checkpoints healthy")

@tool
def taskmanager_metrics(job: str) -> str:
    """Return backpressure and heap usage for the job's TaskManagers."""
    return "backpressure=HIGH on operator 'enrich-join'; heap=92%"

tools = [flink_job_status, taskmanager_metrics]
model = llm.bind_tools(tools)

def agent(state: MessagesState) -> dict:
    sys = {"role": "system", "content": "You are a Flink on-call engineer. Diagnose before recommending."}
    return {"messages": [model.invoke([sys] + state["messages"])]}

b = StateGraph(MessagesState)
b.add_node("agent", agent)
b.add_node("tools", ToolNode(tools))
b.add_edge(START, "agent")
b.add_conditional_edges("agent", tools_condition)  # tool_calls -> "tools", else END
b.add_edge("tools", "agent")                       # <- the loop
graph = b.compile()

out = graph.invoke({"messages": [("user", "orders-enricher is lagging. Why?")]},
                   {"recursion_limit": 12})           # hard cap on loop iterations
for m in out["messages"]:
    m.pretty_print()
```

Keep this file. Chapter 11 replaces it with one line (`create_agent`), and Chapter 16 rebuilds it as a production harness.

## Ch 5. Persistence: checkpointers, threads, time travel

- A **checkpointer** saves a snapshot of state after every super-step. A **thread** (`thread_id`) is one conversation or job, and its checkpoints are its history.
- Persistence is what makes memory, human-in-the-loop, fault recovery and time travel possible. Almost every production feature depends on it.
- Backends: `InMemorySaver` for tests, `SqliteSaver` for local work, `PostgresSaver` for production (Ch 22).
- `durability=` per run sets when checkpoints are written: `"exit"` (fastest, end of run only), `"async"` (default) or `"sync"` (safest).

```python
# ch05_persistence.py - reuse the Ch 4 builder `b`
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from ch04_react_loop import b

conn = sqlite3.connect("ch05.db", check_same_thread=False)
graph = b.compile(checkpointer=SqliteSaver(conn))
cfg = {"configurable": {"thread_id": "INC-4211"}}    # one incident = one thread

graph.invoke({"messages": [("user", "orders-enricher is lagging. Why?")]}, cfg)
graph.invoke({"messages": [("user", "Summarise what you found in 2 lines")]}, cfg)  # remembers turn 1

snap = graph.get_state(cfg)              # latest checkpoint
print(len(snap.values["messages"]), snap.next)

# Time travel: fork from an earlier checkpoint with a different assumption
history = list(graph.get_state_history(cfg))   # newest first
past = history[-3]
fork_cfg = graph.update_state(past.config, {"messages": [("user", "Assume heap is fine; focus on the join.")]})
graph.invoke(None, fork_cfg)                   # continues on a new branch; the original is untouched
```

## Ch 6. Memory: short-term vs long-term (`Store`)

- **Short-term memory** is thread-scoped: the checkpointed `messages`. You manage its size by trimming or summarising (Ch 12 does this with middleware).
- **Long-term memory** is cross-thread: a **`Store`** of JSON documents keyed by a **namespace** tuple, for example `("runbooks", team)` or `("users", user_id)`.
- Nodes and tools reach it through `runtime.store`. Give the store an `index=` embedding config to get semantic `search(query=...)`.
- Three kinds worth storing: **semantic** (facts, preferences), **episodic** (past incidents and how they were solved) and **procedural** (runbooks, prompt rules).

```python
# ch06_store.py - the agent learns runbooks from resolved incidents
from dataclasses import dataclass
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore
from langgraph.checkpoint.memory import InMemorySaver

@dataclass
class Ctx:
    team: str

class S(TypedDict, total=False):
    symptom: str
    resolution: str
    recalled: list[str]

store = InMemoryStore(index={"embed": "openai:text-embedding-3-small", "dims": 1536})

def recall(state: S, runtime: Runtime[Ctx]) -> dict:
    hits = runtime.store.search(("episodes", runtime.context.team), query=state["symptom"], limit=3)
    return {"recalled": [h.value["resolution"] for h in hits]}

def learn(state: S, runtime: Runtime[Ctx]) -> dict:
    if state.get("resolution"):
        runtime.store.put(("episodes", runtime.context.team), state["symptom"][:40],
                          {"symptom": state["symptom"], "resolution": state["resolution"]})
    return {}

b = StateGraph(S, context_schema=Ctx)
b.add_node(recall); b.add_node(learn)
b.add_edge(START, "recall"); b.add_edge("recall", "learn"); b.add_edge("learn", END)
g = b.compile(checkpointer=InMemorySaver(), store=store)

ctx = Ctx(team="streaming")
g.invoke({"symptom": "checkpoint timeout with high backpressure on join",
          "resolution": "raised parallelism of enrich-join 4->8, enabled unaligned checkpoints"},
         {"configurable": {"thread_id": "INC-1"}}, context=ctx)
print(g.invoke({"symptom": "checkpoints failing, join operator backpressured"},
               {"configurable": {"thread_id": "INC-2"}}, context=ctx)["recalled"])  # found via INC-1
```

## Ch 7. Streaming

- `stream_mode` picks what you receive: `"values"` (full state per step), `"updates"` (each node's delta), `"messages"` (LLM tokens plus metadata), `"custom"` (your own events) and `"debug"`.
- Pass a list of modes to get `(mode, chunk)` tuples. Add `subgraphs=True` to see events from nested graphs.
- `get_stream_writer()` lets any node or tool emit progress events. This is how you show "scanning file 40/900" in a UI.
- On `langgraph>=1.1`, `version="v2"` gives typed stream parts with `type`, `ns` and `data` keys.

```python
# ch07_streaming.py
from langgraph.config import get_stream_writer

def scan_manifests(state):
    write = get_stream_writer()
    manifests = [f"m{i}.avro" for i in range(5)]
    for i, m in enumerate(manifests, 1):
        write({"progress": f"scanned {i}/{len(manifests)} manifests"})   # -> "custom" stream
    return {"findings": ["3 manifests reference deleted files"]}

for mode, chunk in graph.stream(inputs, cfg, stream_mode=["updates", "messages", "custom"]):
    if mode == "messages":
        token, meta = chunk
        print(token.content, end="", flush=True)       # live LLM tokens
    elif mode == "custom":
        print("\n[progress]", chunk["progress"])
    else:
        print("\n[update]", list(chunk.keys()))         # which node just finished
```

## Ch 8. Human-in-the-loop: `interrupt` and `Command(resume=)`

- `interrupt(payload)` inside a node pauses the graph, saves a checkpoint and surfaces `payload` to the caller under `__interrupt__`. It needs a checkpointer.
- Resume with `graph.invoke(Command(resume=value), same_config)`. That value becomes the return value of `interrupt()`.
- **Gotcha:** on resume the node **re-runs from its first line**. Keep side effects after the interrupt, or make them idempotent.
- Patterns: approve/reject, edit the tool arguments, review the output, collect missing input.

```python
# ch08_hitl.py - no production restart without a human
from typing import Literal, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver

class S(TypedDict, total=False):
    job: str
    plan: str
    result: str

def propose(state: S) -> dict:
    return {"plan": f"Restart {state['job']} from latest savepoint with parallelism 8"}

def approval(state: S) -> Command[Literal["execute", "__end__"]]:
    decision = interrupt({"question": "Approve this production action?", "plan": state["plan"]})
    if decision["action"] == "approve":
        return Command(goto="execute")
    if decision["action"] == "edit":
        return Command(update={"plan": decision["plan"]}, goto="execute")
    return Command(update={"result": "rejected by human"}, goto=END)

def execute(state: S) -> dict:
    return {"result": f"DONE: {state['plan']}"}        # side effect only after approval

b = StateGraph(S)
b.add_node(propose); b.add_node(approval); b.add_node(execute)
b.add_edge(START, "propose"); b.add_edge("propose", "approval"); b.add_edge("execute", END)
g = b.compile(checkpointer=InMemorySaver())

cfg = {"configurable": {"thread_id": "chg-77"}}
out = g.invoke({"job": "orders-enricher"}, cfg)
print(out["__interrupt__"][0].value)                    # show this to the on-call engineer
print(g.invoke(Command(resume={"action": "edit", "plan": "Restart with parallelism 6"}), cfg))
```

## Ch 9. Subgraphs and composition

- A compiled graph can be a node in another graph. Use this for team ownership, reuse and multi-agent systems.
- **Shared keys:** add the compiled subgraph directly with `add_node("x", subgraph)`. Shared state keys flow in and out automatically.
- **Different schemas:** call the subgraph inside a wrapper node and map state in and out yourself.
- The parent's checkpointer propagates to subgraphs. Interrupts inside a subgraph surface to the parent; stream them with `subgraphs=True`.

```python
# ch09_subgraphs.py - an Iceberg-maintenance team owns its own graph
from typing import TypedDict
from langgraph.graph import StateGraph, START, END

class MaintState(TypedDict, total=False):     # subgraph's private schema
    table: str
    actions: list[str]

def plan_maintenance(s: MaintState) -> dict:
    return {"actions": [f"CALL system.expire_snapshots('{s['table']}')",
                        f"CALL system.rewrite_data_files('{s['table']}')"]}

mb = StateGraph(MaintState)
mb.add_node(plan_maintenance); mb.add_edge(START, "plan_maintenance"); mb.add_edge("plan_maintenance", END)
maintenance = mb.compile()

class OnCallState(TypedDict, total=False):    # parent schema
    incident: str
    table: str
    remediation: list[str]

def run_maintenance(s: OnCallState) -> dict:   # wrapper node maps schemas
    out = maintenance.invoke({"table": s["table"]})
    return {"remediation": out["actions"]}

pb = StateGraph(OnCallState)
pb.add_node(run_maintenance); pb.add_edge(START, "run_maintenance"); pb.add_edge("run_maintenance", END)
print(pb.compile().invoke({"incident": "slow Trino scans", "table": "sales.orders"}))
```

## Ch 10. Functional API and durable execution

- Same runtime, no explicit graph. `@entrypoint` is the workflow; `@task` is a unit of work whose result is **checkpointed**.
- Use it when your logic is ordinary Python control flow (loops, if/else, try/except) and drawing it as a graph adds nothing.
- **Durable execution:** if the process crashes or pauses on `interrupt`, completed tasks are **not re-run** on resume; their saved results are replayed. So put every non-deterministic or side-effecting step inside a `@task`.
- Tasks return futures. Call several, then `.result()` them, to run in parallel.

```python
# ch10_functional.py - durable post-mortem workflow
from langgraph.func import entrypoint, task
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver
from common import llm

@task
def fetch_timeline(incident_id: str) -> str:
    return "14:02 lag alert; 14:09 checkpoint fail; 14:30 restart; 14:41 recovered"

@task
def fetch_metrics(job: str) -> str:
    return f"{job}: peak lag 2.4M, backpressure HIGH 28m"

@task
def draft(timeline: str, metrics: list[str]) -> str:
    return llm.invoke(f"Write a 5-line blameless post-mortem.\nTimeline: {timeline}\nMetrics: {metrics}").content

@entrypoint(checkpointer=InMemorySaver())
def postmortem(incident_id: str) -> dict:
    timeline_f = fetch_timeline(incident_id)                                   # starts now
    metric_fs = [fetch_metrics(j) for j in ["orders-enricher", "orders-sink"]] # in parallel
    doc = draft(timeline_f.result(), [f.result() for f in metric_fs]).result()
    feedback = interrupt({"review": doc})            # tasks above are NOT redone on resume
    return {"postmortem": doc, "review": feedback}

cfg = {"configurable": {"thread_id": "PM-4211"}}
for ev in postmortem.stream("INC-4211", cfg):
    print(ev)
print(postmortem.invoke(Command(resume="LGTM, add action items"), cfg))
```

# Part 2 — Agents

## Ch 11. `create_agent`: the loop, packaged

- `langchain.agents.create_agent` is the Ch 4 loop as a factory, and it returns a **compiled LangGraph graph**. Checkpointers, streaming, interrupts, Studio and deployment all work unchanged.
- Key parameters: `model`, `tools`, `system_prompt`, `middleware`, `response_format`, `context_schema`, `state_schema`, `checkpointer`, `store` and `name`.
- Tools reach the agent's world through **`ToolRuntime`**: `runtime.state`, `runtime.context`, `runtime.store`, `runtime.tool_call_id` and `runtime.stream_writer`. The `runtime` argument is hidden from the model.
- A tool may return a `Command(update=...)` to write to agent state, not just text.

```python
# ch11_create_agent.py
from dataclasses import dataclass
from langchain.agents import create_agent
from langchain.tools import tool, ToolRuntime
from langgraph.checkpoint.memory import InMemorySaver
from common import MODEL
from ch04_react_loop import flink_job_status, taskmanager_metrics

@dataclass
class Ctx:
    user_id: str
    team: str

@tool
def my_jobs(runtime: ToolRuntime[Ctx]) -> str:
    """List the Flink jobs owned by the caller's team."""
    owned = {"streaming": ["orders-enricher", "orders-sink"], "ml": ["feature-writer"]}
    return ", ".join(owned.get(runtime.context.team, []))

agent = create_agent(
    model=MODEL,
    tools=[my_jobs, flink_job_status, taskmanager_metrics],
    system_prompt="You are the streaming on-call assistant. Check the caller's jobs, diagnose, then recommend.",
    context_schema=Ctx,
    checkpointer=InMemorySaver(),
    name="flink_oncall",
)

res = agent.invoke({"messages": [{"role": "user", "content": "Are any of my jobs unhealthy?"}]},
                   {"configurable": {"thread_id": "u42-s1"}},
                   context=Ctx(user_id="u42", team="streaming"))
print(res["messages"][-1].content)
```

## Ch 12. Middleware: the harness hook points

Middleware is how you engineer **around** the loop without rewriting it. Every production concern (budgets, retries, guardrails, approvals, context control, observability) is one middleware.

| Hook | Runs | Style | Typical harness use |
| --- | --- | --- | --- |
| `before_agent` | Once, at start of a run | Node | Load memory, validate input, auth |
| `before_model` | Before every model call | Node | Trim or summarise context, budget checks, early exit (`jump_to`) |
| `wrap_model_call` | Around every model call | Wrap | Retry, fallback, dynamic model, prompt and tool selection, caching |
| `after_model` | After every model response | Node | Output guardrails, HITL on tool calls, stop conditions |
| `wrap_tool_call` | Around every tool call | Wrap | Policy and permission checks, retries, audit log, timeouts |
| `after_agent` | Once, at end of a run | Node | Persist memory, emit metrics, final validation |

- **Order:** `before_*` hooks run first-to-last, `after_*` hooks run last-to-first, and `wrap_*` hooks nest like onions (the first one is outermost).
- **Node-style** hooks return a state update dict (or `None`). **Wrap-style** hooks receive `handler` and may call it zero times (short-circuit), once, or many times (retry).
- Middleware can add **state keys** (`state_schema`) and **tools** (`tools = [...]`). That is how Deep Agents add todos and files.

**Built-in middleware you will use constantly** (`from langchain.agents.middleware import ...`)

| Middleware | Solves |
| --- | --- |
| `SummarizationMiddleware` | Context overflow: summarises old turns at a token, message or fraction trigger |
| `ContextEditingMiddleware` | Clears old tool outputs when context grows |
| `HumanInTheLoopMiddleware` | Pauses for approve, edit or reject on chosen tools (needs a checkpointer) |
| `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware` | Runaway loops and cost: per-run and per-thread caps |
| `ModelRetryMiddleware` / `ToolRetryMiddleware` | Transient failures, with exponential backoff |
| `ModelFallbackMiddleware` | Provider outage: fall through to other models |
| `PIIMiddleware` | Redact, mask, hash or block emails, cards, keys and similar |
| `LLMToolSelectorMiddleware` | Large tool sets: a cheap model pre-selects the relevant tools |
| `TodoListMiddleware` | Planning: adds a `write_todos` tool and todo state |

```python
# ch12_middleware.py - a production-shaped agent in ~40 lines
import re, time, logging
from langchain.agents import create_agent
from langchain.agents.middleware import (
    SummarizationMiddleware, HumanInTheLoopMiddleware, ModelCallLimitMiddleware,
    ToolCallLimitMiddleware, ModelRetryMiddleware, ModelFallbackMiddleware, PIIMiddleware,
    wrap_tool_call,
)
from langchain.tools import tool
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from common import MODEL

log = logging.getLogger("audit")

@tool
def run_trino_query(sql: str) -> str:
    """Run a read-only Trino SQL query against the lakehouse."""
    return "rows=3 | sales.orders | 1240 snapshots | 412 GB"

@tool
def restart_job(job: str, parallelism: int) -> str:
    """Restart a Flink job from its latest savepoint with the given parallelism."""
    return f"{job} restarted with parallelism={parallelism}"

@wrap_tool_call                       # custom guardrail + audit, one hook
def sql_policy_and_audit(request, handler):
    call = request.tool_call
    if call["name"] == "run_trino_query" and re.search(r"\b(DROP|DELETE|TRUNCATE|ALTER)\b", call["args"]["sql"], re.I):
        return ToolMessage(content="BLOCKED by policy: read-only SQL only.", tool_call_id=call["id"])
    t0 = time.perf_counter()
    result = handler(request)
    log.info({"tool": call["name"], "args": call["args"], "ms": round((time.perf_counter() - t0) * 1000)})
    return result

agent = create_agent(
    model=MODEL,
    tools=[run_trino_query, restart_job],
    checkpointer=InMemorySaver(),
    middleware=[
        SummarizationMiddleware(model="anthropic:claude-haiku-4-5", trigger=("tokens", 60_000), keep=("messages", 20)),
        ModelCallLimitMiddleware(run_limit=15, exit_behavior="end"),     # loop budget
        ToolCallLimitMiddleware(tool_name="run_trino_query", run_limit=5),
        ModelRetryMiddleware(max_retries=3),
        ModelFallbackMiddleware("openai:gpt-5.5"),
        PIIMiddleware("email", strategy="redact"),
        HumanInTheLoopMiddleware(interrupt_on={"restart_job": {"allowed_decisions": ["approve", "edit", "reject"]}}),
        sql_policy_and_audit,
    ],
)

cfg = {"configurable": {"thread_id": "chg-91"}}
out = agent.invoke({"messages": [("user", "orders-enricher is lagging; restart it at parallelism 8")]}, cfg)
print(out["__interrupt__"])                                     # pending restart_job approval
agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)
```

## Ch 13. Context engineering and structured output

- **Context engineering** means controlling exactly what the model sees on each call: the prompt, the tools, the messages and the model itself. In `create_agent` you do it in `wrap_model_call` and `@dynamic_prompt`.
- `request.override(model=..., tools=..., messages=..., system_prompt=...)` changes the call for **this step only**. The stored state stays untouched.
- **Structured output:** `response_format=PydanticModel` puts a validated object in `result["structured_response"]`. `ProviderStrategy` uses the provider's native JSON schema mode; `ToolStrategy` emulates it with a tool call (works with any tool-calling model).

```python
# ch13_context.py
from typing import Literal
from pydantic import BaseModel, Field
from langchain.agents import create_agent
from langchain.agents.middleware import dynamic_prompt, wrap_model_call, ModelRequest
from langchain.chat_models import init_chat_model
from common import MODEL
from ch11_create_agent import Ctx, my_jobs
from ch12_middleware import run_trino_query, restart_job

class IncidentReport(BaseModel):
    severity: Literal["P1", "P2", "P3"]
    root_cause: str = Field(description="One sentence, evidence-backed")
    actions: list[str]
    needs_human: bool

@dynamic_prompt
def role_prompt(request: ModelRequest) -> str:
    team = request.runtime.context.team
    return f"You are on-call for team '{team}'. Cite tool evidence for every claim. Never guess metrics."

big = init_chat_model("anthropic:claude-opus-4-6")

@wrap_model_call
def adapt(request: ModelRequest, handler):
    tools = request.tools
    if request.runtime.context.team != "streaming":             # least privilege per caller
        tools = [t for t in tools if t.name != "restart_job"]
    model = big if len(request.messages) > 25 else request.model  # escalate on long investigations
    return handler(request.override(tools=tools, model=model))

agent = create_agent(model=MODEL, tools=[my_jobs, run_trino_query, restart_job],
                     context_schema=Ctx, middleware=[role_prompt, adapt],
                     response_format=IncidentReport)

r = agent.invoke({"messages": [("user", "Triage: Trino scans on sales.orders 5x slower since Monday")]},
                 context=Ctx(user_id="u7", team="analytics"))
report: IncidentReport = r["structured_response"]
print(report.model_dump_json(indent=2))
```

## Ch 14. Multi-agent patterns

| Pattern | How | When |
| --- | --- | --- |
| **Subagents as tools** (supervisor) | Wrap each agent in a `@tool`; the supervisor calls them | Default. Clean context isolation, easy to trace |
| **Handoffs** | A tool returns `Command(goto=agent, graph=Command.PARENT)` | The specialist should take over the conversation |
| **Router** | Classify once, then dispatch to one agent (conditional edge or `Send`) | Distinct domains, single-hop questions |
| **Custom graph** | Agents are nodes in your own `StateGraph` | Fixed pipelines (plan, then execute, then verify) |

- The real question in every pattern is **context**: what each agent sees, and what comes back. Subagents return a **summary**, not their full message history.
- Give each specialist a narrow toolset and a sharp `description`. The supervisor routes on descriptions.

```python
# ch14_supervisor.py - supervisor over two specialists
from langchain.agents import create_agent
from langchain.tools import tool
from common import MODEL
from ch04_react_loop import flink_job_status, taskmanager_metrics
from ch12_middleware import run_trino_query

flink_agent = create_agent(MODEL, tools=[flink_job_status, taskmanager_metrics],
                           system_prompt="Flink specialist. Diagnose lag, checkpoints, backpressure. Reply in <=5 bullets.")
iceberg_agent = create_agent(MODEL, tools=[run_trino_query],
                             system_prompt="Iceberg/Trino specialist. Diagnose snapshots, small files, partitions. Reply in <=5 bullets.")

@tool
def ask_flink_specialist(question: str) -> str:
    """Delegate streaming questions: Flink jobs, lag, checkpoints, backpressure."""
    return flink_agent.invoke({"messages": [("user", question)]})["messages"][-1].content

@tool
def ask_iceberg_specialist(question: str) -> str:
    """Delegate lakehouse questions: Iceberg tables, Trino performance, compaction."""
    return iceberg_agent.invoke({"messages": [("user", question)]})["messages"][-1].content

supervisor = create_agent(MODEL, tools=[ask_flink_specialist, ask_iceberg_specialist],
    system_prompt="Incident commander. Split the problem, delegate, then merge into one root-cause analysis.")

print(supervisor.invoke({"messages": [("user",
    "Dashboards are stale and Trino is slow on sales.orders. orders-enricher writes that table.")]})
    ["messages"][-1].content)
```

# Part 3 — Harness engineering on LangGraph

## Ch 15. Harness anatomy, mapped to LangGraph

The **model** decides; the **harness** is everything else that makes those decisions safe, bounded, observable and correct. LangGraph gives you a primitive for every harness component, so you rarely need to invent one.

| Harness component | Question it answers | LangGraph / LangChain primitive |
| --- | --- | --- |
| Loop control | When do we stop? | Conditional edges, `recursion_limit`, `ModelCallLimitMiddleware`, `jump_to: "end"` |
| Action space | What can it do? | Tools, `ToolNode`, `LLMToolSelectorMiddleware`, per-request tool filtering |
| Context management | What does it see each step? | `wrap_model_call`, `SummarizationMiddleware`, `ContextEditingMiddleware`, filesystem offload (Ch 17) |
| State and memory | What does it remember? | Checkpointer (thread), `Store` (cross-thread) |
| Guardrails and policy | What must it never do? | `wrap_tool_call` policies, `PIIMiddleware`, `interrupt` / HITL |
| Resilience | What if a step fails? | `RetryPolicy` on nodes, `ModelRetry` / `ModelFallback` / `ToolRetry` middleware, durable execution |
| Budgets | How much may it spend? | Step and tool counters in state, call-limit middleware, timeouts |
| Verification | Is the answer right? | A verifier node or `after_agent` hook (evaluator-optimizer loop) |
| Observability | What happened, and why? | LangSmith traces, `custom` stream events, audit middleware |

**Two ways to build a harness**

- **Composition** (`create_agent` + middleware): fastest, idiomatic, and it is what Deep Agents does. Use it by default.
- **Explicit graph** (`StateGraph`): full control over the loop topology. Use it when you need non-standard stages such as plan, act, verify, reflect, or parallel critics. Ch 16 builds one so you see every moving part.

## Ch 16. Building a custom harness from scratch

Goal: an incident-investigation harness with **step and tool budgets**, **node-level retries**, **tool-error containment**, a **verifier loop** with feedback, and a **forced answer** when the budget runs out.

- `RetryPolicy(max_attempts, initial_interval, backoff_factor, retry_on)` on a node retries transient failures without your own code.
- `ToolNode(..., handle_tool_errors=True)` turns tool exceptions into `ToolMessage`s, so the model can recover instead of the run crashing.
- `CachePolicy(ttl=...)` plus `compile(cache=...)` memoises expensive, deterministic nodes such as metadata scans.
- Budgets live **in state**, so they are checkpointed, visible in traces and survive resumes.

```python
# ch16_harness.py - act -> tools -> act ... -> verify -> (retry with feedback | end)
from typing import Annotated, Literal, TypedDict
from pydantic import BaseModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, RetryPolicy
from langgraph.checkpoint.memory import InMemorySaver
from common import llm
from ch04_react_loop import flink_job_status, taskmanager_metrics
from ch12_middleware import run_trino_query

TOOLS = [flink_job_status, taskmanager_metrics, run_trino_query]
MAX_STEPS, MAX_TOOL_CALLS, MAX_ATTEMPTS = 10, 8, 2

class HState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    tool_calls: int
    attempts: int
    verdict: str

class Verdict(BaseModel):
    ok: bool
    feedback: str

SYSTEM = SystemMessage("You are an on-call agent. Gather evidence with tools, then state root cause "
                       "and next actions. Every claim must cite a tool result.")
actor = llm.bind_tools(TOOLS)
judge = llm.with_structured_output(Verdict)

def act(state: HState) -> Command[Literal["tools", "verify"]]:
    over_budget = state["steps"] >= MAX_STEPS or state["tool_calls"] >= MAX_TOOL_CALLS
    prompt = [SYSTEM, *state["messages"]]
    if over_budget:                                       # no tools => model must answer
        prompt.append(HumanMessage("Budget exhausted. Answer now with the evidence you have."))
    msg = (llm if over_budget else actor).invoke(prompt)
    update = {"messages": [msg], "steps": state["steps"] + 1,
              "tool_calls": state["tool_calls"] + len(msg.tool_calls)}
    return Command(update=update, goto="tools" if msg.tool_calls else "verify")

def verify(state: HState) -> Command[Literal["act", "__end__"]]:
    answer = state["messages"][-1].text
    evidence = "\n".join(m.text for m in state["messages"] if m.type == "tool")
    v = judge.invoke(f"EVIDENCE:\n{evidence}\n\nANSWER:\n{answer}\n\n"
                     "Pass only if every claim is supported by evidence and there is a concrete next action.")
    if v.ok or state["attempts"] + 1 >= MAX_ATTEMPTS:
        return Command(update={"verdict": "pass" if v.ok else "fail-unverified"}, goto=END)
    return Command(update={"attempts": state["attempts"] + 1,
                           "messages": [HumanMessage(f"Reviewer feedback: {v.feedback}. Revise.")]},
                   goto="act")

b = StateGraph(HState)
b.add_node("act", act, retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0))  # flaky LLM API
b.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))                           # contain tool errors
b.add_node("verify", verify)
b.add_edge(START, "act")
b.add_edge("tools", "act")
harness = b.compile(checkpointer=InMemorySaver())

out = harness.invoke(
    {"messages": [HumanMessage("orders-enricher lags and sales dashboards are stale. Root cause?")],
     "steps": 0, "tool_calls": 0, "attempts": 0, "verdict": ""},
    {"configurable": {"thread_id": "h-1"}, "recursion_limit": 50},   # outer safety net
)
print(out["verdict"], out["steps"], out["tool_calls"])
print(out["messages"][-1].text)
```

**Exercises (harness thinking)**

1. Make `run_trino_query` raise on every second call and confirm the loop recovers.
2. Add a `cost_usd` key and a reducer that sums `usage_metadata` from each AI message. Stop at $0.50.
3. Rebuild the same harness with `create_agent`: `ModelCallLimitMiddleware` for budgets, a `wrap_tool_call` for errors, and an `after_agent` hook that runs the verifier and uses `jump_to` to loop back. Compare the two traces in LangSmith.

# Part 4 — Deep Agents

## Ch 17. Deep Agents: concepts

A "shallow" tool loop (Ch 4, Ch 11) breaks down on long tasks. Its context fills with raw tool output, it loses the plan, and it cannot split work. **Deep Agents** (`deepagents`) is LangChain's opinionated **harness** on top of `create_agent` that fixes this with a fixed middleware stack.

| Pillar | Mechanism | Harness problem solved |
| --- | --- | --- |
| Planning | `write_todos` tool (`TodoListMiddleware`) | Keeps the plan in state, so it survives context churn |
| Filesystem | `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep` over a pluggable **backend** | **Context offload**: big outputs go to files, not messages |
| Subagents | `task` tool spawns isolated agents (sync or async) | **Context isolation**: a subagent burns tokens, the parent gets a summary |
| Detailed prompt | A long built-in system prompt that teaches the tools | Behaviour comes from instructions, not from more code |
| Memory and skills | `AGENTS.md` (always loaded), `SKILL.md` dirs (loaded on demand) | Procedural memory without a giant prompt |
| Steering | `interrupt_on`, `permissions`, summarisation, prompt caching | Safety and cost at long horizons |

- The result is still a **compiled LangGraph graph**, so Part 1 (checkpoints, streaming, HITL) and Part 5 (deployment) apply unchanged.
- **Version note:** on recent releases (0.7+) planning is opt-in. Add `TodoListMiddleware()` to `middleware=`. On older releases `write_todos` is built in.
- Shell `execute` exists only with a **sandbox backend** (Modal, Daytona, Runloop and similar). Never give a deep agent a real shell on your laptop.

## Ch 18. The `deepagents` API: backends, subagents, steering

- `create_deep_agent(model, tools, system_prompt, subagents, middleware, backend, store, checkpointer, interrupt_on, memory, skills, permissions, response_format, ...)`
- **Backends** decide where files live: `StateBackend` (default, in thread state), `FilesystemBackend(root_dir, virtual_mode=True)` (real disk, sandboxed to root), `StoreBackend` (LangGraph Store, cross-thread), and `CompositeBackend(default=..., routes={"/prefix/": ...})`, where the longest matching prefix wins.
- **Subagent spec** (dict): `name`, `description` (the parent routes on it), `system_prompt`, plus optional `tools`, `model`, `middleware`, `interrupt_on`, `skills`, `permissions`, `response_format` and `mode` (`"isolated"` or `"fork"`). Use `CompiledSubAgent(name, description, runnable)` to plug in any graph you built.

```python
# ch18_deep_agent.py - incident commander with two investigators
from deepagents import create_deep_agent, CompiledSubAgent
from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend
from langchain.agents.middleware import TodoListMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from common import MODEL
from ch04_react_loop import flink_job_status, taskmanager_metrics
from ch12_middleware import run_trino_query, restart_job
from ch16_harness import harness            # your own graph can be a subagent

subagents = [
    {
        "name": "flink-investigator",
        "description": "Investigates ONE Flink job (lag, checkpoints, backpressure). Pass the job name.",
        "system_prompt": "Investigate the job. Write raw evidence to /findings/<job>.md. "
                         "Return a 5-line summary with the root-cause hypothesis.",
        "tools": [flink_job_status, taskmanager_metrics],
    },
    {
        "name": "lakehouse-investigator",
        "description": "Investigates Iceberg table health and Trino performance for ONE table.",
        "system_prompt": "Use read-only SQL. Write evidence to /findings/<table>.md. Return a 5-line summary.",
        "tools": [run_trino_query],
        "model": "anthropic:claude-haiku-4-5",        # cheaper model for narrow work
    },
    CompiledSubAgent(name="verified-rca",
                     description="Produces a budgeted, self-verified root cause for a well-scoped question.",
                     runnable=harness),
]

agent = create_deep_agent(
    model=MODEL,
    tools=[restart_job],
    system_prompt=("You are the incident commander for the data platform. Plan with todos, delegate one "
                   "investigation per component in parallel, read /findings/*, then write /report.md. "
                   "Save durable lessons to /memories/lessons.md."),
    subagents=subagents,
    middleware=[TodoListMiddleware()],                # planning (built in on older versions)
    backend=lambda rt: CompositeBackend(
        default=FilesystemBackend(root_dir="./oncall_ws", virtual_mode=True),   # scratch on disk, jailed
        routes={"/memories/": StoreBackend(rt)},                               # survives across threads
    ),
    store=InMemoryStore(),
    checkpointer=InMemorySaver(),
    interrupt_on={"restart_job": True},               # human approval for prod actions
    memory=["/AGENTS.md"],                            # always-loaded team conventions (resolved by the backend)
    skills=["/skills/"],                              # SKILL.md folders, loaded on demand
)

cfg = {"configurable": {"thread_id": "INC-4211"}}
msg = {"messages": [{"role": "user", "content":
       "Sales dashboards stale since 14:00. orders-enricher lagging; sales.orders scans slow. Find root cause, propose fix."}]}

for ns, chunk in agent.stream(msg, cfg, stream_mode="updates", subgraphs=True):
    print(ns or "main", list(chunk))                  # watch subagents work in their own namespaces

state = agent.get_state(cfg)
if state.interrupts:                                   # restart_job is waiting for a human
    agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)
```

**Version note:** the backend factory form `lambda rt: ...` above is documented for long-term memory. Newer releases also accept backend instances directly (`StateBackend()`, `StoreBackend(...)`). Match the form to your installed version.

## Ch 19. Memory, skills and permissions: production deep agents

- **Long-term memory:** route `/memories/` to a `StoreBackend` (Postgres-backed in production). Tell the agent in its prompt what to save there. It reads and writes memory with the same file tools it already knows.
- **`AGENTS.md`** is always in context: team conventions, escalation rules, "never do X". Keep it short.
- **Skills** use progressive disclosure. Only the `SKILL.md` frontmatter (name and description) is loaded at startup; the body and any scripts are read only when relevant. Put runbooks here, not in the system prompt.
- **Permissions** are ordered allow/deny rules over paths; the first match wins and no match means allow. Subagent permissions **replace** the parent's. They do not apply to sandbox backends, where the sandbox itself is the boundary.
- **Async subagents** (0.5+) run in the background. The parent launches, checks, updates or cancels them while it keeps talking to the user.

```markdown
<!-- oncall_ws/skills/flink-backpressure/SKILL.md -->
---
name: flink-backpressure
description: Diagnose and fix Flink backpressure and checkpoint timeouts. Use when lag grows and checkpoints fail.
---
1. Find the first backpressured operator (taskmanager_metrics).
2. If heap > 85%: suspect state growth; check TTL config before scaling.
3. If a join is hot: raise its parallelism one step (x1.5); enable unaligned checkpoints.
4. Never restart without a savepoint. Restart requires human approval.
```

```python
# ch19_permissions.py - read-only auditor subagent; memories are append-only for humans
from deepagents import create_deep_agent, FilesystemPermission
from deepagents.backends import FilesystemBackend
from common import MODEL

RO = [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]

agent = create_deep_agent(
    model=MODEL,
    permissions=[
        FilesystemPermission(operations=["read", "write"], paths=["/.env", "/secrets/**"], mode="deny"),
        FilesystemPermission(operations=["write"], paths=["/skills/**", "/AGENTS.md"], mode="deny"),
        FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="allow"),
    ],
    subagents=[{"name": "auditor", "description": "Read-only review of findings and report.",
                "system_prompt": "Check /report.md against /findings/*. List unsupported claims.",
                "permissions": RO}],
    backend=lambda rt: FilesystemBackend(root_dir="./oncall_ws", virtual_mode=True),
)
```

**Deep-agent design checklist**

- [ ] Every subagent returns a summary of 10 lines or fewer and writes raw evidence to files.
- [ ] Durable knowledge goes to `/memories/` (Store); scratch goes to state or disk.
- [ ] Destructive tools are in `interrupt_on`; secrets and policy files are denied by `permissions`.
- [ ] Runbooks are skills, not prompt text.
- [ ] A checkpointer is set (required for HITL and resume), and every run is traced in LangSmith.

# Part 5 — Production

## Ch 20. LangSmith: tracing and evaluation

- **Tracing:** set `LANGSMITH_TRACING=true` and every node, model call, tool call and middleware hook becomes a span. Add `metadata` and `tags` in the run config so you can filter by tenant, version or incident.
- **Offline evals:** dataset, then a target function, then evaluators, then an experiment. Run them on every prompt, model or middleware change, the same way you run CI tests.
- **Agent-specific evals:** final answer (correctness), **trajectory** (did it call the right tools in a sane order; see the `agentevals` package) and **single step** (given this state, is the next tool call right?).
- **Online evals:** attach LLM-as-judge or rule evaluators to production traces, and send failures to an annotation queue. Those failures become the next dataset rows.

```python
# ch20_evals.py
from langsmith import Client
from common import llm
from ch11_create_agent import agent, Ctx

client = Client()
ds = client.create_dataset("oncall-rca-v1")
client.create_examples(dataset_id=ds.id, examples=[
    {"inputs": {"q": "orders-enricher lagging, checkpoints failing"},
     "outputs": {"must_mention": "backpressure", "must_call": "taskmanager_metrics"}},
    {"inputs": {"q": "Are any of my jobs unhealthy?"},
     "outputs": {"must_mention": "orders-enricher", "must_call": "my_jobs"}},
])

def target(inputs: dict) -> dict:
    r = agent.invoke({"messages": [("user", inputs["q"])]},
                     {"configurable": {"thread_id": inputs["q"][:20]}},
                     context=Ctx(user_id="eval", team="streaming"))
    tools = [tc["name"] for m in r["messages"] if m.type == "ai" for tc in m.tool_calls]
    return {"answer": r["messages"][-1].text, "tools": tools}

def mentions_key_fact(outputs, reference_outputs) -> bool:          # answer check
    return reference_outputs["must_mention"].lower() in outputs["answer"].lower()

def used_right_tool(outputs, reference_outputs) -> bool:            # trajectory check
    return reference_outputs["must_call"] in outputs["tools"]

def efficient(outputs) -> dict:                                      # budget check
    return {"key": "tool_calls", "score": len(outputs["tools"])}

client.evaluate(target, data="oncall-rca-v1",
                evaluators=[mentions_key_fact, used_right_tool, efficient],
                experiment_prefix="prompt-v2", max_concurrency=4)
```

## Ch 21. Deployment: Agent Server and LangSmith Deployment

- **Agent Server** is the runtime API for your graphs: assistants, threads, runs, a task queue, persistence, streaming, crons, webhooks, auth and an MCP endpoint. **LangSmith Deployment** (formerly LangGraph Platform) hosts it for you. You can also self-host it, for example on EKS, with Postgres and Redis.
- `langgraph.json` declares the graphs, dependencies, env, auth and store or checkpointer settings. `langgraph dev` runs it locally with hot reload and opens **Studio**.
- **Do not pass a checkpointer or store** to graphs you deploy. The server injects durable Postgres-backed ones.
- Core API objects: **Assistant** (a graph plus config, so you can version prompts and models without redeploying), **Thread** (state), **Run** (one execution, streamed or in the background), **Cron** (scheduled run).
- **Double-texting:** a user sends a new message while a run is still in flight. `multitask_strategy` decides what happens: `reject`, `enqueue`, `interrupt` or `rollback`.

```json
// langgraph-lab.json
{
  "dependencies": ["."],
  "graphs": {
    "oncall":  "./src/oncall/deep_agent.py:agent",
    "harness": "./src/oncall/harness.py:harness"
  },
  "env": ".env",
  "auth": { "path": "./src/oncall/auth.py:auth" }
}
```

```bash
langgraph-lab dev                 # local server + Studio
langgraph-lab build -t oncall:1.0 # Docker image for self-hosting (needs DATABASE_URI, REDIS_URI, license)
langgraph-lab deploy              # push to LangSmith Deployment
```

```python
# ch21_client.py - talk to the server like your UI or another service would
import asyncio
from langgraph_sdk import get_client

async def main():
    client = get_client(url="http://localhost:2024")       # or the deployment URL + api_key
    thread = await client.threads.create(metadata={"incident": "INC-4211"})
    async for ev in client.runs.stream(thread["thread_id"], "oncall",
            input={"messages": [{"role": "user", "content": "orders-enricher lagging, investigate"}]},
            stream_mode="updates", multitask_strategy="enqueue"):
        print(ev.event, ev.data)

    # nightly lakehouse health audit, results posted to a webhook
    await client.crons.create("harness", schedule="30 1 * * *",
        input={"messages": [{"role": "user", "content": "Audit top-20 tables for snapshot/small-file debt"}],
               "steps": 0, "tool_calls": 0, "attempts": 0, "verdict": ""},
        webhook="https://hooks.internal/oncall-audit")

asyncio.run(main())
```

```python
# src/oncall/auth.py - every user sees only their own threads
from langgraph_sdk import Auth
auth = Auth()

@auth.authenticate
async def authenticate(authorization: str | None) -> Auth.types.MinimalUserDict:
    user = await verify_jwt(authorization)          # your IdP (Okta, Cognito, etc.)
    return {"identity": user["sub"], "team": user["team"]}

@auth.on
async def owner_only(ctx: Auth.types.AuthContext, value: dict):
    value.setdefault("metadata", {})["owner"] = ctx.user.identity   # stamp on create
    return {"owner": ctx.user.identity}                              # filter on read
```

## Ch 22. Persistence in production: Postgres, durability, TTL

- Use `PostgresSaver` / `AsyncPostgresSaver` for checkpoints and `PostgresStore` for long-term memory (with pgvector for semantic search). Call `.setup()` once to run migrations.
- **Durability vs latency:** `durability="sync"` for money-moving or production-changing graphs, `"async"` as the default, `"exit"` for cheap, idempotent batch graphs.
- **Encrypt** checkpoints at rest with `EncryptedSerializer` (state often holds tool output with customer data). Set **TTL** on threads and checkpoints, either in `langgraph.json` or with a cleanup job.
- **Retries and idempotency:** a resumed run replays pending writes, so tools that change the world need idempotency keys (use the `tool_call_id`).

```python
# ch22_postgres.py
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.store.postgres import PostgresStore
from ch16_harness import b                     # the uncompiled harness builder

DB = "postgresql://agent:***@pg.internal:5432/agents?sslmode=require"
serde = EncryptedSerializer.from_pycryptodome_aes()   # key from LANGGRAPH_AES_KEY

with PostgresSaver.from_conn_string(DB) as cp, PostgresStore.from_conn_string(DB) as store:
    cp.serde = serde
    cp.setup(); store.setup()                   # idempotent migrations
    graph = b.compile(checkpointer=cp, store=store)
    init_state = {"messages": [("user", "Why is orders-enricher lagging?")], "steps": 0, "tool_calls": 0, "attempts": 0, "verdict": ""}
    graph.invoke(init_state, {"configurable": {"thread_id": "INC-4211"}}, durability="sync")
```

## Ch 23. MCP: tools from anywhere, agents for anyone

- **Consume:** `langchain-mcp-adapters` turns MCP servers (stdio or streamable HTTP) into LangChain tools. Any agent, harness or deep agent can use them.
- **Expose:** a deployed Agent Server serves your graphs at an `/mcp` endpoint, so Claude, IDEs or other agents can call your agent as a tool.
- Treat MCP tools as **untrusted input**. Wrap them with the same `wrap_tool_call` policy, approvals and limits as your own tools.

```python
# ch23_mcp.py
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ToolCallLimitMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from common import MODEL

async def main():
    mcp = MultiServerMCPClient({
        "trino":     {"command": "python", "args": ["./mcp/trino_server.py"], "transport": "stdio"},
        "pagerduty": {"url": "https://mcp.internal/pagerduty/mcp", "transport": "streamable_http",
                      "headers": {"Authorization": "Bearer ${PD_TOKEN}"}},
    })
    tools = await mcp.get_tools()
    agent = create_agent(MODEL, tools=tools, checkpointer=InMemorySaver(), middleware=[
        ToolCallLimitMiddleware(run_limit=20),
        HumanInTheLoopMiddleware(interrupt_on={"create_incident": True, "resolve_incident": True}),
    ])
    r = await agent.ainvoke({"messages": [("user", "Which tables in sales.* grew >50% this week?")]},
                            {"configurable": {"thread_id": "mcp-1"}})
    print(r["messages"][-1].text)

asyncio.run(main())
```

## Ch 24. Guardrails and testing

**Guardrails are layered:** input, then action, then output, then runtime.

| Layer | Hook | Example |
| --- | --- | --- |
| Input | `before_agent` | Prompt-injection classifier, input size limits, auth check |
| Action | `wrap_tool_call`, `interrupt_on`, `permissions` | Read-only SQL, approvals for restarts, path deny lists |
| Output | `after_model`, `after_agent`, `response_format` | PII redaction, schema validation, citation check |
| Runtime | Sandbox backend, scoped credentials, call limits | No shell on hosts, least-privilege IAM, budget caps |

**Testing pyramid for agents**

1. **Unit:** call node functions directly with hand-made state. They are plain Python.
2. **Graph:** run the graph with a **fake model** that emits scripted tool calls. Assert on routing, budgets and interrupts. Deterministic, free and fast.
3. **Evals:** real model plus a LangSmith dataset (Ch 20), gated in CI on score thresholds.

```python
# test_oncall.py - pytest; no API keys needed
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from ch12_middleware import restart_job

class FakeToolModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):      # fake model ignores tool schemas
        return self

def scripted(*msgs):
    return FakeToolModel(messages=iter(msgs))

def test_restart_is_blocked_until_approved():
    model = scripted(
        AIMessage(content="", tool_calls=[{"name": "restart_job", "id": "c1",
                                           "args": {"job": "orders-enricher", "parallelism": 8}}]),
        AIMessage(content="Restart rejected; escalating to owner."),
    )
    agent = create_agent(model, tools=[restart_job], checkpointer=InMemorySaver(),
                         middleware=[HumanInTheLoopMiddleware(interrupt_on={"restart_job": True})])
    cfg = {"configurable": {"thread_id": "t1"}}

    out = agent.invoke({"messages": [("user", "restart orders-enricher")]}, cfg)
    assert out["__interrupt__"], "restart must pause for approval"

    out = agent.invoke(Command(resume={"decisions": [{"type": "reject", "message": "freeze window"}]}), cfg)
    assert not any(m.type == "tool" and "restarted" in m.text for m in out["messages"])

def test_policy_blocks_destructive_sql():
    from ch12_middleware import sql_policy_and_audit
    class Req:   # minimal stand-in for ToolCallRequest
        tool_call = {"name": "run_trino_query", "id": "c9", "args": {"sql": "DROP TABLE sales.orders"}}
    res = sql_policy_and_audit.wrap_tool_call(Req(), handler=lambda r: (_ for _ in ()).throw(AssertionError("tool ran")))
    assert "BLOCKED" in res.content
```

# Capstones and pacing

## Capstone projects

Each capstone reuses the chapter code and adds exactly one new production concern.

1. **Lakehouse Doctor (Ch 1–10, 16):** a nightly graph that fans out with `Send` over the top 50 Iceberg tables, checks snapshot and small-file debt, verifies its own findings, and pauses for approval before running any `CALL system.*` maintenance. It runs as a cron.
2. **Build your own mini Deep Agent (Ch 11–12, 17):** on plain `create_agent`, write three middlewares yourself: a `write_todos` tool plus todo state, a virtual filesystem kept in state, and a `task` tool that spawns an isolated subagent. Then diff your version against `deepagents`. This is the best harness-engineering exercise in the curriculum.
3. **Incident Commander (Ch 18–19, 21–23):** the Ch 18 deep agent with Postgres memory, skills-as-runbooks, PagerDuty and Trino over MCP, JWT auth, and deployment to Agent Server (self-hosted on EKS or LangSmith).
4. **Agent quality gate (Ch 20, 24):** a CI job that runs fake-model graph tests plus a LangSmith eval on 30 real incidents. It blocks the merge if correctness or the tool-call budget regresses.

## Suggested pacing (about 6 weeks, 1–2 hours a day)

| Week | Chapters | Done when |
| --- | --- | --- |
| 1 | 1–6 | You can explain super-steps, reducers and checkpoints, and fork a thread with time travel |
| 2 | 7–11 | The HITL restart flow works end-to-end; the functional post-mortem survives a crash |
| 3 | 12–14 | You have written two custom middlewares and a supervisor with two specialists |
| 4 | 15–16, Capstone 2 | Your custom harness enforces budgets and verification; your mini Deep Agent runs |
| 5 | 17–19, 23 | The Incident Commander runs locally with skills, memory, permissions and MCP |
| 6 | 20–22, 24, Capstones 3–4 | It is deployed, evaluated in CI and backed by Postgres |

**Open questions to confirm against your installed versions:** whether planning is opt-in (`TodoListMiddleware`) in your `deepagents` release, and whether backends are passed as instances or `lambda rt:` factories. The APIs above follow the docs as of November 2026.

## Sources

- [LangGraph changelog (v1.1 type-safe streaming, deepagents 0.4/0.5)](https://docs.langchain.com/oss/python/langgraph/changelog-py)
- [langgraph releases (1.2.x, CLI 0.4.x)](https://releases.sh/langchain/langgraph-releases.md)
- [What's new in LangChain v1 (`create_agent`, middleware)](https://docs.langchain.com/oss/python/releases/langchain-v1.md)
- [Custom middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom) · [Built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview) · [Subagents](https://docs.langchain.com/oss/python/deepagents/subagents) · [Long-term memory](https://docs.langchain.com/oss/python/deepagents/long-term-memory) · [Permissions](https://docs.langchain.com/oss/python/deepagents/permissions)
- [`create_deep_agent` reference](https://reference.langchain.com/python/deepagents/graph/create_deep_agent)
- [Application structure and `langgraph.json`](https://docs.langchain.com/oss/python/langgraph/application-structure/index.html) · [LangSmith Deployment components](https://docs.langchain.com/langsmith/components/index.html)
