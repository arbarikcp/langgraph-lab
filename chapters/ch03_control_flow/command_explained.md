# `triage` and `Command`, explained

Notes on `incident_router.py`. Every behaviour below was run against langgraph 1.2.20.

## 1. What `triage` does

```python
def triage(state: Incident) -> Command[Literal["page_oncall", "open_ticket"]]:
    if state["severity"] == "P1":
        return Command(update={"action": "page"}, goto="page_oncall")
    return Command(update={"action": "ticket"}, goto="open_ticket")
```

It reads `severity` (already set by the input, Ch 1 did the classifying), and in a **single return**:

- **updates state**: `action = "page"` or `"ticket"`
- **picks the next node**: `page_oncall` for P1, `open_ticket` for everything else

Notice that `build_graph()` has **no edge out of `triage`**. The `Command` *is* the routing.

## 2. `Command` = update + goto

A normal node returns a dict, which only updates state. Where to go next is decided elsewhere, by edges.
A node that returns `Command(...)` does both jobs itself:

| Field | Meaning |
| --- | --- |
| `update` | Same as the dict a normal node returns. Merged into state at the barrier, following the same channel rules (`LastValue`, reducers) |
| `goto` | Node name (or a list of names, or `END`) to run in the **next super-step** |
| `graph` | `Command.PARENT` routes in the parent graph from inside a subgraph (Ch 9) |
| `resume` | Value passed back into an `interrupt()` when resuming (Ch 8). Used as *input* to `invoke`, not returned from a node |

## 3. How it works under the hood

Remember from Ch 1: every node is woken by its own edge channel, `branch:to:<node>`:

```text
triage       triggers=['branch:to:triage']
page_oncall  triggers=['branch:to:page_oncall']
open_ticket  triggers=['branch:to:open_ticket']
```

A static edge `add_edge("a", "b")` means "when `a` finishes, **always** write `branch:to:b`".
`Command(goto="page_oncall")` means "for **this** run, write `branch:to:page_oncall`". Same mechanism, but chosen at runtime by your code.

For a P1 incident:

| Step | Runs | Writes at the barrier | Effect |
| --- | --- | --- | --- |
| 1 | `triage` | `action="page"` + `branch:to:page_oncall` | `page_oncall` is scheduled |
| 2 | `page_oncall` | `log += ["PAGED ..."]` | its static edge goes to `END` |

`open_ticket`'s channel is never written, so it never runs.

## 4. Why the `Literal[...]` annotation matters

The return type `Command[Literal["page_oncall", "open_ticket"]]` is **not enforced at runtime**. It only tells `compile()` and the graph drawing which targets exist. Here's the same graph without it (`-> Command`):

- It **still runs correctly**: P1 → `page_oncall`.
- But the drawing shows the edges as `triage → __end__` only, so the picture lies about your graph.

With the `Literal`, the drawing shows dotted (conditional) edges from `triage` to both targets.

## 5. Gotchas (all verified)

| Do this | What happens | Lesson |
| --- | --- | --- |
| `goto="pagr_oncall"` (typo) | Only a **warning**: `wrote to unknown channel branch:to:pagr_oncall, ignoring it`. The run ends quietly, nobody gets paged | The `Literal` doesn't validate `goto`. Typos fail silently, so test your routes |
| Keep `Command(goto="page_oncall")` **and** add `add_edge("triage", "open_ticket")` | **Both** run in step 2 | `Command` adds to static edges, it doesn't replace them. Don't mix the two on one node |
| `goto=["page_oncall", "open_ticket"]` | Both run in parallel in step 2. `log` has both lines thanks to the `operator.add` reducer | A list fans out. Parallel writes to a `LastValue` key would raise `InvalidUpdateError` |

## 6. When to use `Command` vs a conditional edge

| | Conditional edge | `Command` |
| --- | --- | --- |
| Where the routing logic lives | A separate router function on the edge | Inside the node |
| Can update state while routing | No (a router only returns a name) | Yes |
| Graph wiring | `add_conditional_edges("triage", router)` | No edge out of the node, plus a `Literal` annotation |
| Best for | Pure decisions ("retry or stop?"), e.g. `retry_loop.should_retry` | "Do work, record the result, and decide where to go" |
| Also needed for | | Jumping to the parent graph (`Command.PARENT`, Ch 9) and multi-agent handoffs, where an agent hands control and context to another in one step |

Rule of thumb: if you'd otherwise write a node that sets a field **and** a router that immediately reads that same field, merge them into one `Command`.

## 7. The same router written with a conditional edge, for comparison

```python
def triage(state):                       # node: only updates
    return {"action": "page" if state["severity"] == "P1" else "ticket"}

def route(state) -> Literal["page_oncall", "open_ticket"]:   # router: only decides
    return "page_oncall" if state["action"] == "page" else "open_ticket"

builder.add_conditional_edges("triage", route)
```

Two functions and a wiring call, versus one `Command`. Both produce identical super-steps.
