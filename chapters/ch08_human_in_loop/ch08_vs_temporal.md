# Is LangGraph (Ch 8 / Ch 10) basically Temporal?

**Short answer:** yes, the *idea* is the same. Ch 8 and Ch 10 are **durable workflows**: state is persisted, the run can pause for an outside event (a human), and it resumes later by ID, replaying finished work instead of redoing it. That's Temporal's core idea too.

But LangGraph (the open-source library you're using) is a **durable-execution library with no engine**, while Temporal is a **durable-execution engine**. The biggest difference: in Temporal, a server *drives* the workflow forward. In LangGraph, **nothing happens until your code calls `invoke()` again**.

---

## 1. Side-by-side mapping

| Concept | LangGraph (this repo) | Temporal |
|---|---|---|
| A workflow instance's identity | `thread_id` (`"CHG-2"`) | Workflow ID |
| Where progress is saved | Checkpointer (`InMemorySaver`, SQLite, Postgres) | Temporal server's **event history** |
| Orchestration code | Graph nodes + edges (Ch 8), or the `@entrypoint` function (Ch 10) | `@workflow.defn` class, `@workflow.run` method |
| Unit of side-effecting work | A separate node (`execute`), or a `@task` (Ch 10) | **Activity** (`@activity.defn`) |
| "Wait for a human" | `interrupt(payload)`: saves state and **returns** to the caller | `await workflow.wait_condition(...)` waiting on a **signal/update** |
| Delivering the human's answer | `graph.invoke(Command(resume=decision), cfg)` | `handle.signal(...)` / `handle.execute_update(...)` |
| "What's waiting?" | `graph.get_state(cfg).next` → `('approval',)` | Query handlers, Temporal UI, visibility/search |
| Recovery after a crash | Someone calls `invoke(None, cfg)` | **Automatic**: another worker picks it up |
| Replay on resume | Ch 8: re-run the paused **node** from its top. Ch 10: re-run the **entrypoint** from the top, finished `@task`s return saved results | Re-run the **workflow function** from the top, completed activities return their recorded results |
| Rule this forces on you | Side effects and nondeterminism go in their own node / `@task` | Side effects and nondeterminism go in activities. Workflow code must be deterministic |

**Ch 10 is the closest match.** `@entrypoint` ≈ workflow, `@task` ≈ activity, "body re-runs, finished tasks replay" ≈ Temporal replay. Ch 10's "Break it" table (calling `sources.publish` outside a task → duplicate wiki pages) is exactly the bug Temporal users get when they do I/O in workflow code.

---

## 2. The same Ch 8 workflow written in Temporal (Python SDK sketch)

```python
import asyncio
from datetime import timedelta
from temporalio import activity, workflow

@activity.defn
async def get_metrics(job: str) -> dict: ...                  # ≈ propose's metrics read

@activity.defn
async def restart(job: str, parallelism: int) -> str: ...     # ≈ execute node

@workflow.defn
class RestartApproval:
    def __init__(self) -> None:
        self.decision: dict | None = None

    @workflow.signal
    def decide(self, decision: dict) -> None:                 # ≈ Command(resume=decision)
        self.decision = decision

    @workflow.run
    async def run(self, job: str) -> str:
        m = await workflow.execute_activity(get_metrics, job,
                                            start_to_close_timeout=timedelta(seconds=30))
        parallelism = m["parallelism"] * 2

        try:                                                  # ≈ interrupt(...)
            await workflow.wait_condition(lambda: self.decision is not None,
                                          timeout=timedelta(hours=4))
        except asyncio.TimeoutError:
            return "auto-rejected: nobody answered in 4h"     # durable timer: LangGraph has none

        if self.decision["action"] == "reject":
            return "rejected by on-call"
        if self.decision["action"] == "edit":
            parallelism = self.decision["parallelism"]
        return await workflow.execute_activity(restart, args=[job, parallelism],
                                               start_to_close_timeout=timedelta(minutes=10))
```

And the caller side:

```python
handle = await client.start_workflow(RestartApproval.run, "orders-enricher",
                                     id="CHG-2", task_queue="ops")
# ... hours later, from a Slack button handler in a different process:
await client.get_workflow_handle("CHG-2").signal(RestartApproval.decide,
                                                 {"action": "edit", "parallelism": 6})
```

Notice the shape: Temporal's workflow **sits waiting** (logically, not in a thread) and the *server* wakes it up when the signal arrives. LangGraph's graph has already **returned**, and your code has to call `invoke` again.

---

## 3. Real differences

### Who drives execution
- **Temporal:** a server plus workers polling task queues. When a signal arrives, a timer fires, or a worker dies, the server schedules the next step on some worker. The workflow moves forward on its own.
- **LangGraph OSS:** a library inside your process. The checkpointer is just storage. If the process crashes halfway, the thread sits there until *you* notice and call `invoke(None, cfg)` (Ch 10 shows this by hand). There's no scheduler, worker pool, or task queue.
  - *LangGraph Platform / `langgraph-api` server* adds a run queue, background runs and resume endpoints, which closes some of this gap. It's still not a general-purpose workflow engine.

### Durable timers
- Temporal: `await workflow.sleep(timedelta(days=30))` or the `wait_condition(..., timeout=...)` above survive restarts. "Auto-reject if nobody answers in 4h" is one line.
- LangGraph: no durable timers. You'd need a cron job that finds stale paused threads and resumes them with `{"action": "reject"}`.

### Retries and timeouts
- Temporal: per-activity retry policy (backoff, max attempts, non-retryable errors), plus start-to-close, schedule-to-close and heartbeat timeouts, all enforced by the server.
- LangGraph: `RetryPolicy` on nodes / tasks (`builder.add_node(execute, retry_policy=RetryPolicy(max_attempts=3))`, `@task(retry_policy=...)`). It retries **in-process only**: if the process dies, the retry dies with it.

### How progress is recorded
- Temporal: an **event history** (activity scheduled, completed, signal received…). Replay rebuilds the workflow's in-memory state by re-running the code against that history.
- LangGraph Graph API (Ch 8): **state snapshots** after each super-step. Resume loads the last snapshot and re-runs only the interrupted node. It's coarser, but simpler: no full replay.
- LangGraph Functional API (Ch 10): close to Temporal. It stores each task's result and replays the entrypoint.

### Is determinism enforced?
- Temporal detects nondeterminism on replay ("this code scheduled activity X but history says Y") and fails the workflow task. The Python SDK also runs workflows in a sandbox.
- LangGraph trusts you. Put an LLM call directly in a Ch 10 entrypoint body and it silently re-runs, possibly with a different answer, on every resume.

### Code changes while runs are paused (versioning)
- Temporal: `workflow.patched("v2")` / worker versioning lets old in-flight runs follow old code paths.
- LangGraph: nothing built in. If you rename a node while threads are paused on it, resuming them breaks. You manage that yourself (drain first, or keep old node names).

### Things LangGraph has that Temporal doesn't
- **LLM-shaped state:** message lists with reducers (`add_messages`), tool-call loops (`ToolNode`, Ch 4), token streaming (Ch 7).
- **Editing state on a paused run:** `graph.update_state(cfg, {...})`, plus time travel / forking from any earlier checkpoint (`get_state_history`). Temporal's closest equivalent is *reset* to an earlier event, which is blunter.
- **Lightweight:** `pip install` and SQLite. No cluster to run. Interrupts are built for "show the human the proposed tool call and let them approve/edit/reject it".

### Same caveat in both: exactly-once is your job
Both give **at-least-once** execution for side-effecting steps. If `ops.restart()` succeeds but the process dies before the result is saved, it runs again on retry. Real side effects need an **idempotency key** (for example, the change ID `CHG-2`) on the external API.

---

## 4. When to use which

| Situation | Pick |
|---|---|
| An agent loop (LLM ↔ tools) with occasional human approval, short-ish lifetimes, one service | **LangGraph** |
| Business process lasting days or weeks, timers/SLAs, must self-heal after crashes, many services, high volume | **Temporal** |
| Both: a long-running ops process where *some steps* are AI agents | **Temporal outside, LangGraph inside**. Run the LangGraph agent inside a Temporal activity, and put the human approval and timers in the Temporal workflow |

A practical mental model:

> **Temporal** = durable execution as *infrastructure* (server-driven, enforced, general purpose).
> **LangGraph** = durable execution as a *library feature* (caller-driven, by convention, built for LLM agents).

Ch 8 teaches the LangGraph version of "pause for a signal". Ch 10 teaches the LangGraph version of "activities are recorded and replayed". If you know Temporal, the rules you already follow (side effects in activities, deterministic orchestration code, idempotent activities) carry straight over.
