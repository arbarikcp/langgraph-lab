# `ToolNode` and `handle_tool_errors`, explained

Every behaviour below was run against langgraph 1.2.20 (source: `langgraph/prebuilt/tool_node.py`).

## 1. What `ToolNode` is

`ToolNode(TOOLS)` is a ready-made graph node. In `agent_loop.py` it's the `tools` node:

```python
builder.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))
```

Each time it runs (one super-step), it:

1. **Reads the last message** in `state["messages"]` and takes its `tool_calls`. That's the `AIMessage` the `agent` node just produced.
2. **Looks up each call by name** in its `{name: tool}` map, which is built once in `__init__` from `TOOLS`.
3. **Validates the args** against the tool's schema (e.g. `job: str`).
4. **Runs every call in parallel** on a thread pool (`executor.map`). Three 1-second tool calls took 1.0 s, not 3 s.
5. **Returns one `ToolMessage` per call**, each with the matching `tool_call_id`, `name`, `content` (the tool's return value, as a string) and `status` (`"success"` or `"error"`).
6. Those messages are appended through the `add_messages` reducer, and the static edge `tools → agent` sends them back to the model.

```text
AIMessage(tool_calls=[c1: flink_job_status, c2: taskmanager_metrics])
        │  ToolNode: look up → validate → run c1 ∥ c2
        ▼
ToolMessage(tool_call_id=c1, status=success, "lag 2,400,000, checkpoint FAILED")
ToolMessage(tool_call_id=c2, status=success, "backpressure HIGH on enrich-join ...")
```

The `tool_call_id` matters: most chat APIs (OpenAI, Anthropic) require every tool call to get exactly one answer with the same id, or the next model call fails.

Other constructor options: `name` (default `"tools"`, the name `tools_condition` routes to), `messages_key` (if your state uses another key), and `wrap_tool_call` (an interceptor, see §4).

Note: in 1.2.20 you can't call `ToolNode(...).invoke(...)` on its own. It raises `Missing required config key 'N/A' for 'tools'`. Run it inside a graph, as `test_agent_loop.py` does.

## 2. Two kinds of failure

| Kind | Example | Raised as |
| --- | --- | --- |
| **The model's mistake**: bad args or an unknown tool | `{"jobname": "b"}` instead of `{"job": ...}`; tool `"nope"` | `ToolInvocationError` (the tool never runs) |
| **The tool's own failure**: an exception while it runs | `RuntimeError("Flink REST API 503")` | Whatever the tool raised |

`handle_tool_errors` decides which of these become an error `ToolMessage` and which crash the run.

## 3. `handle_tool_errors` options

| Value | Catches | `ToolMessage.content` |
| --- | --- | --- |
| *(default)* | **Only the model's mistakes**. Tool exceptions are re-raised and crash the run | `Error invoking tool 'ok' with kwargs {'jobname': 'b'} with error: job: Field required. Please fix your mistakes.` |
| `True` | Every exception | `Error: RuntimeError('Flink REST API 503')\n Please fix your mistakes.` |
| `"some text"` | Every exception | Exactly that text, e.g. `"Tool failed, try another approach."` |
| `(TimeoutError, ConnectionError)` | Only those types. Anything else crashes | Default template |
| `def h(e: RuntimeError) -> str` | Types taken from the **type hint** of `e` | Whatever `h` returns |
| `False` | Nothing. Even bad args crash, with `ToolInvocationError` | none |

Unknown tool names never crash: you always get `Error: nope is not a valid tool, try one of [flaky, ok].`

### One failing call among several

The model asks for `flaky` and `ok` in the same turn:

| Setting | Result |
| --- | --- |
| Default | **Whole run crashes** with `RuntimeError`. `ok` did run (`ok` calls = 1), but its result is thrown away |
| `True` | `c0 status=error "Error: RuntimeError(...)"`, `c1 status=success "ok b"`. The run continues |

So with `True`, one broken tool doesn't cost you the results of the others.

## 4. Does it call the tool again after a failure? **No.**

`handle_tool_errors` **never retries**. Verified: with `True`, `flaky` was called exactly **once**. All it does is turn the exception into an error `ToolMessage`.

What happens next is up to **the model**:

```text
step N    tools  → ToolMessage(status=error, "Error: RuntimeError('Flink REST API 503') Please fix your mistakes.")
step N+1  agent  → the model reads the error and decides:
                    • call the same tool again (a "retry", but chosen by the LLM), or
                    • try a different tool / different args, or
                    • answer, reporting that the data was unavailable
```

That "Please fix your mistakes" suffix is a nudge for the model to correct its args. For a real outage like a 503, that's misleading. A custom message via `str` or a callable (e.g. `"Flink is down. Don't retry, report it."`) steers the model better.

### If you want a real retry, there are two ways

| Mechanism | What re-runs | Verified |
| --- | --- | --- |
| `ToolNode(..., wrap_tool_call=retry3)`: an interceptor that calls `execute(request)` up to 3 times | **Only the failing call**. Healthy calls run once | `flaky` ×3, `ok` ×1, then an error `ToolMessage` (with `handle_tool_errors=True`) |
| `add_node("tools", ToolNode(...), retry_policy=RetryPolicy(max_attempts=3, retry_on=RuntimeError))` | **The whole node**, so every tool call in that turn | `flaky` ×3 **and `ok` ×3**. Healthy tools re-run too, so side effects repeat |

```python
def retry3(request, execute):
    for _ in range(3):
        result = execute(request)          # runs the tool; may be called many times
        if result.status != "error":
            return result
    return result                           # give up: hand the error to the model
```

For `wrap_tool_call` to see failures as `status="error"` instead of exceptions, combine it with `handle_tool_errors=True` (or a type list). Prefer it over `RetryPolicy` whenever tools have side effects (paging someone, restarting a job).

## 5. Summary

- `ToolNode` = for the last `AIMessage`: look up each tool, validate args, run all calls in parallel, return one `ToolMessage` per `tool_call_id`.
- **Default**: the model's mistakes (bad args, unknown tool) come back as error messages; tool exceptions crash the run.
- `handle_tool_errors=True`: every exception becomes `ToolMessage(status="error")`, so the run continues and other calls keep their results.
- **No automatic retry.** The model sees the error and decides what to do. For real retries, use `wrap_tool_call` (per call) or `RetryPolicy` (whole node, re-runs every call).
