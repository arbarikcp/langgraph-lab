from langgraph.types import Command

from chapters.ch08_human_in_loop.restart_approval import OpsContext, build_graph
from chapters.ch08_human_in_loop.sim.flink_ops import FlinkOps


def run(decision, ops: FlinkOps) -> dict:
    graph, ctx = build_graph(), OpsContext(ops)
    cfg = {"configurable": {"thread_id": str(decision)}}
    paused = graph.invoke({"job": "orders-enricher"}, cfg, context=ctx)
    assert paused["__interrupt__"][0].value["parallelism"] == 8
    return graph.invoke(Command(resume=decision), cfg, context=ctx)


def test_edit_changes_what_gets_executed():
    ops = FlinkOps()
    assert "parallelism 6" in run({"action": "edit", "parallelism": 6}, ops)["result"]
    assert ops.restarts == ["orders-enricher@6"]


def test_reject_executes_nothing():
    ops = FlinkOps()
    assert run({"action": "reject"}, ops)["result"] == "rejected by on-call"
    assert ops.restarts == []
