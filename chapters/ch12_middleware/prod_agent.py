"""Ch 12: a production-shaped agent — built-in middleware stack + human approval for restarts.

Runs anywhere with a scripted model; add --ollama to let the real LLM drive.
Run:  python -m chapters.ch12_middleware.prod_agent [--ollama]
"""
import sys

from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    PIIMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from chapters.ch12_middleware.custom_middleware import audit_log, sql_policy
from chapters.ch12_middleware.ops_tools import TOOLS
from chapters.ch12_middleware.sim import lakehouse_ops
from chapters.ch12_middleware.sim.scripted_model import ScriptedChatModel, call

SYSTEM_PROMPT = ("You are the lakehouse on-call assistant. Check the table with run_trino_query, "
                 "then restart the job if asked. Be brief.")


def prod_middleware() -> list:
    return [
        ModelCallLimitMiddleware(run_limit=6, exit_behavior="end"),          # loop budget per run
        ToolCallLimitMiddleware(tool_name="run_trino_query", run_limit=3),   # per-tool budget
        ToolRetryMiddleware(max_retries=2, tools=["run_trino_query"],        # flaky Trino coordinator
                            initial_delay=0.1, jitter=False),
        PIIMiddleware("email", strategy="redact"),                           # user input, before it is stored
        HumanInTheLoopMiddleware(interrupt_on={                              # pause before restart_job runs
            "restart_job": {"allowed_decisions": ["approve", "edit", "reject"]},
        }),
        audit_log,                                                           # custom, from custom_middleware
        sql_policy,
    ]


def build_prod_agent(model):
    return create_agent(model=model, tools=TOOLS, system_prompt=SYSTEM_PROMPT,
                        middleware=prod_middleware(), checkpointer=InMemorySaver())


# --- demo -------------------------------------------------------------------------------------

INCIDENT = ("orders-enricher is lagging. Check sales.orders, then restart orders-enricher at "
            "parallelism 8. Page me at maria.oncall@example.com when done.")
RUNAWAY = "Keep querying sales.orders until the snapshot count drops below 100."


def incident_script() -> list[AIMessage]:
    return [AIMessage("", tool_calls=[call("run_trino_query", sql="SELECT * FROM sales.orders")]),
            AIMessage("", tool_calls=[call("restart_job", job="orders-enricher", parallelism=8)]),
            AIMessage("Checked sales.orders and sent the restart for approval. Paging is up to you.")]


def make_model(script: list[AIMessage], use_ollama: bool):
    if use_ollama:
        from common.llm import llm
        return llm
    return ScriptedChatModel(replies=script)


def show(messages, last: int | None = None) -> None:
    for msg in messages[-last:] if last else messages:
        if msg.type == "ai" and msg.tool_calls:
            print(f"  [ai] calls {[f'{c['name']}({c['args']})' for c in msg.tool_calls]}")
        elif msg.type == "tool":
            print(f"  [tool:{msg.status}] {msg.content.splitlines()[-1]}")
        else:
            print(f"  [{msg.type}] {msg.content}")


def approval_thread(thread_id: str, decision: dict, use_ollama: bool) -> None:
    lakehouse_ops.OPS = lakehouse_ops.LakehouseOps(trino_failures=1)        # first query fails once
    agent = build_prod_agent(make_model(incident_script(), use_ollama))
    cfg = {"configurable": {"thread_id": thread_id}}

    out = agent.invoke({"messages": [{"role": "user", "content": INCIDENT}]}, cfg)
    show(out["messages"])
    print(f"  Trino calls: {len(lakehouse_ops.OPS.queries)} (one failed, retried by ToolRetryMiddleware)")
    request = out["__interrupt__"][0].value["action_requests"][0]
    print(f"  ⏸  paused, next={agent.get_state(cfg).next}: approve {request['name']}({request['args']})?")

    print(f"  ▶  resume with {decision}")
    out = agent.invoke(Command(resume={"decisions": [decision]}), cfg)
    show(out["messages"], last=2)
    print(f"  restarts actually executed: {lakehouse_ops.OPS.restarts}")


def main() -> None:
    use_ollama = "--ollama" in sys.argv
    agent = build_prod_agent(make_model(incident_script(), use_ollama))
    print("graph nodes (node-style hooks only; wrap-style hooks run inside model/tools):")
    print("  " + "\n  ".join(n for n in agent.get_graph().nodes if n not in ("__start__", "__end__")))

    print("\n1) CHG-91: PII redaction, Trino retry, approval → edit to parallelism 6")
    approval_thread("CHG-91", {"type": "edit", "edited_action": {
        "name": "restart_job", "args": {"job": "orders-enricher", "parallelism": 6}}}, use_ollama)

    print("\n2) CHG-92: same request → reject")
    approval_thread("CHG-92", {"type": "reject", "message": "Not during peak hours."}, use_ollama)

    print("\n3) runaway loop: ToolCallLimit(run_trino_query=3), then ModelCallLimit(6) ends the run")
    lakehouse_ops.OPS = lakehouse_ops.LakehouseOps()
    loop_script = [AIMessage("", tool_calls=[call("run_trino_query", sql="SELECT * FROM sales.orders")])]
    agent = build_prod_agent(make_model(loop_script, use_ollama))
    out = agent.invoke({"messages": [{"role": "user", "content": RUNAWAY}]},
                       {"configurable": {"thread_id": "LOOP-1"}})
    statuses = [m.status for m in out["messages"] if m.type == "tool"]
    print(f"  tool results: {statuses}")
    print(f"  Trino calls that really ran: {len(lakehouse_ops.OPS.queries)}")
    print(f"  last message: {out['messages'][-1].content}")


if __name__ == "__main__":
    main()
