# Ch 9 — Subgraphs and composition

The Iceberg-maintenance team owns its own graph (plan → approve → run). The on-call graph uses it in both styles: **shared keys** and a **wrapper node**. The child pauses for approval; the interrupt surfaces through the parent. No LLM.

## Concept

- A **compiled graph can be a node** of another graph. Use it for team ownership, reuse, and (later) multi-agent systems.
- **Shared keys:** `add_node("maintenance", child_graph)`. Keys with the **same name** in both schemas flow in and out automatically. Other keys are invisible to the child.
- **Different schemas:** a **wrapper node** calls `child.invoke({...})` and maps state in and out by hand. More code, but the two schemas are fully decoupled.
- The child is compiled **without** a checkpointer: it inherits the parent's. Runtime context also flows into the child.
- An `interrupt()` inside the child **surfaces to the parent** (`__interrupt__`); resume the **parent** with `Command(resume=...)`. Inspect the paused child with `get_state(cfg, subgraphs=True).tasks[0].state`.
- On resume, only the interrupted **child node** re-runs; earlier child nodes do not. With a wrapper, the **wrapper re-runs from its first line** (the child then resumes from its own checkpoint).
- Stream child events with `subgraphs=True` (Ch 7): each event carries the child's namespace.

## Files

| File | What |
| --- | --- |
| `maintenance_team.py` | Child graph (`plan_maintenance`, `approve_and_run`), style A (`build_shared_keys_graph`), style B (`build_wrapper_graph`), demo |
| `sim/iceberg_catalog.py` | `IcebergCatalog`: table health + `call()` that records every procedure run |
| `test_maintenance_team.py` | Shared-keys interrupt + resume; wrapper mapping |

## Diagrams

**Parent / child nesting**

```mermaid
flowchart LR
  subgraph A["style A: OnCallState"]
    T[triage] --> M
    subgraph M["maintenance (compiled child, added as node)"]
      P[plan_maintenance] --> R[approve_and_run<br/>interrupt]
    end
    M --> W[write_report]
  end
  subgraph B["style B: TicketState"]
    X["run_maintenance (wrapper)<br/>maintenance.invoke({table: target_table})"]
  end
```

**State mapping**

```mermaid
flowchart LR
  subgraph OnCallState
    o1[incident]
    o2[table]
    o3[executed]
    o4[report]
  end
  subgraph MaintState
    m1[table]
    m2[actions]
    m3[executed]
  end
  subgraph TicketState
    t1[ticket]
    t2[target_table]
    t3[remediation]
  end
  o2 <-- "same name: automatic" --> m1
  o3 <-- "same name: automatic" --> m3
  t2 -- "wrapper maps in" --> m1
  m3 -- "wrapper maps out" --> t3
```

**Interrupt inside the child, resume through the parent**

```mermaid
sequenceDiagram
  participant H as human
  participant P as parent graph
  participant C as child (maintenance)
  participant K as checkpointer
  H->>P: invoke(incident, thread INC-4300)
  P->>P: triage → table
  P->>C: table
  C->>C: plan_maintenance → actions
  C->>C: approve_and_run: interrupt(actions)
  C->>K: child checkpoint (next = approve_and_run)
  P->>K: parent checkpoint (next = maintenance)
  P-->>H: __interrupt__
  H->>P: Command(resume=True)
  P->>C: resume (triage and plan_maintenance do NOT re-run)
  C->>C: approve_and_run re-runs → catalog.call ×2
  C-->>P: executed
  P->>P: write_report
  P-->>H: report
```

**Classes**

```mermaid
classDiagram
  class MaintState {
    <<child schema>>
    table: str
    actions: list[str]
    executed: list[str]
  }
  class OnCallState {
    <<style A parent>>
    incident: str
    table: str
    executed: list[str]
    report: str
  }
  class TicketState {
    <<style B parent>>
    ticket: str
    target_table: str
    remediation: list[str]
  }
  class CatalogContext {
    <<runtime context, shared>>
    catalog: IcebergCatalog
  }
  class IcebergCatalog {
    executed: list[str]
    health(table) dict
    call(procedure) str
  }
  OnCallState ..> MaintState : shares table, executed
  TicketState ..> MaintState : wrapper maps
  CatalogContext --> IcebergCatalog
```

## Run it

```bash
uv run python -m chapters.ch09_subgraphs.maintenance_team
uv run pytest chapters/ch09_subgraphs
```

Look for: `[child ]` events in the stream, `parent next=('maintenance',), child next=('approve_and_run',)` while paused, and in style B the wrapper line printed **twice** but `plan_maintenance (child)` only **once**.

## Break it

Checked against langgraph 1.2.20:

| Change | What happens | Lesson |
| --- | --- | --- |
| Rename the parent's `table` to `table_name` (style A) | `KeyError: 'table'` inside the child | Shared-key style matches by **name** only |
| Rename the child's `executed` to `done` (style A and B) | No error. Procedures **do run** on the catalog, but the parent reports `0 maintenance procedures run` / `remediation=[]` | Output keys that don't match are **silently dropped**. Test the contract |
| Resume with `Command(resume=False)` | `0 maintenance procedures run`, catalog untouched | The child's decision logic, driven from the parent |
| Compile the parent without a checkpointer | Pause works; resume → `RuntimeError` (as in Ch 8) | The child relies on the parent's checkpointer |
| Stream style A without `subgraphs=True` | Only `triage` and the parent `__interrupt__`; no `plan_maintenance` event | Child internals are hidden by default |
