"""Ch 8: no production restart without a human — interrupt(), then Command(resume=...).

Run:  python -m chapters.ch08_human_in_loop.restart_approval                 # scripted decisions
      python -m chapters.ch08_human_in_loop.restart_approval --interactive   # you decide
"""
import sys
from dataclasses import dataclass
from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from chapters.ch08_human_in_loop.sim.flink_ops import FlinkOps


class ChangeState(TypedDict, total=False):
    job: str
    parallelism: int        # proposed, may be edited by the human
    reason: str
    result: str


@dataclass
class OpsContext:
    ops: FlinkOps


class Decision(TypedDict, total=False):
    action: Literal["approve", "edit", "reject"]
    parallelism: int        # only for "edit"


def propose(state: ChangeState, runtime: Runtime[OpsContext]) -> dict:
    m = runtime.context.ops.metrics(state["job"])
    return {"parallelism": m["parallelism"] * 2,
            "reason": f"backpressure {m['backpressure']} on {m['hot_operator']}"}


def approval(state: ChangeState) -> Command[Literal["execute", "__end__"]]:
    print("   · approval node: running from its first line")   # prints twice: before pause and on resume
    decision: Decision = interrupt({                            # pause here; the value comes back on resume
        "question": "Approve this production restart?",
        "job": state["job"],
        "parallelism": state["parallelism"],
        "reason": state["reason"],
    })
    if decision["action"] == "approve":
        return Command(goto="execute")
    if decision["action"] == "edit":
        return Command(update={"parallelism": decision["parallelism"]}, goto="execute")
    return Command(update={"result": "rejected by on-call"}, goto=END)


def execute(state: ChangeState, runtime: Runtime[OpsContext]) -> dict:
    # The side effect lives after the approval, in its own node: it runs once, only if approved.
    return {"result": runtime.context.ops.restart(state["job"], state["parallelism"])}


def build_graph(checkpointer=None):
    builder = StateGraph(ChangeState, context_schema=OpsContext)
    builder.add_node(propose)
    builder.add_node(approval)
    builder.add_node(execute)
    builder.add_edge(START, "propose")
    builder.add_edge("propose", "approval")
    builder.add_edge("execute", END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())   # interrupts need a checkpointer


def ask_human(request: dict) -> Decision:
    answer = input("   approve / edit N / reject > ").strip().split()
    if answer and answer[0] == "edit":
        return {"action": "edit", "parallelism": int(answer[1])}
    return {"action": answer[0] if answer else "reject"}


def main() -> None:
    interactive = "--interactive" in sys.argv
    #interactive = True
    ops = FlinkOps()
    ctx = OpsContext(ops=ops)
    graph = build_graph()
    print(graph.get_graph().draw_mermaid())
    scripted: list[Decision] = [{"action": "approve"},
                                {"action": "edit", "parallelism": 6},
                                {"action": "reject"}]

    for i, scripted_decision in enumerate(scripted, 1):
        cfg = {"configurable": {"thread_id": f"CHG-{i}"}}
        print(f"\n▶ thread CHG-{i}: propose a restart of orders-enricher")
        out = graph.invoke({"job": "orders-enricher"}, cfg, context=ctx)

        request = out["__interrupt__"][0].value
        print(f"   paused. next={graph.get_state(cfg).next}  request={request}")
        decision = ask_human(request) if interactive else scripted_decision
        print(f"   human -> {decision}")

        final = graph.invoke(Command(resume=decision), cfg, context=ctx)
        print(f"   result: {final['result']}")

    print(f"\nrestarts actually executed: {ops.restarts}")


if __name__ == "__main__":
    main()
