"""The explicit harness recovers, verifies and stops; the create_agent rebuild behaves the same."""
import io
from contextlib import redirect_stdout

from langchain_core.messages import HumanMessage

from chapters.ch16_custom_harness import agent_rebuild
from chapters.ch16_custom_harness import rca_harness as r
from chapters.ch16_custom_harness.sim import platform
from chapters.ch16_custom_harness.sim.scripted_model import ScriptedChatModel


def investigate(graph, thread: str, script: list, limits: r.Limits = r.Limits(), fail_every: int = 0):
    platform.TRINO = platform.FakeTrino(fail_every=fail_every)
    model = ScriptedChatModel(replies=script)
    with redirect_stdout(io.StringIO()):
        return r.investigate(graph, thread, r.RCAContext(model, r.rule_judge, limits)), model


def test_recovers_from_flaky_model_and_tool_then_verifies_and_caches_prefetch():
    platform.CATALOG = platform.FakeCatalog()
    graph = r.build_harness()
    out, model = investigate(graph, "a", r.flaky_run_script(), fail_every=2)
    assert (out["verdict"], out["attempts"], out["steps"], model.calls) == ("pass", 2, 6, 7)
    assert sum(m.type == "tool" and m.status == "error" for m in out["messages"]) == 1
    investigate(graph, "b", r.flaky_run_script(), fail_every=2)
    assert platform.CATALOG.loads == 1


def test_cost_budget_forces_an_answer_without_tool_calls():
    out, _ = investigate(r.build_harness(), "c", r.cost_run_script(), r.Limits(max_cost_usd=0.03, max_attempts=1))
    assert out["stop_reason"] == "cost" and out["steps"] == 4
    assert out["messages"][-1].tool_calls == [] and out["verdict"] == "pass"


def test_create_agent_rebuild_gives_the_same_answer():
    graph_out, _ = investigate(r.build_harness(), "d", r.flaky_run_script(), fail_every=2)
    platform.TRINO = platform.FakeTrino(fail_every=2)
    agent = agent_rebuild.build_agent(ScriptedChatModel(replies=r.flaky_run_script()))
    agent_out = agent.invoke({"messages": [HumanMessage(r.QUESTION)]}, {"recursion_limit": 50})
    assert agent_out["verdict"] == "pass" and agent_out["attempts"] == 2
    assert agent_out["messages"][-1].content == graph_out["messages"][-1].content
