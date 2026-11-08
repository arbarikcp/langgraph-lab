"""Backend routing across threads, and the commander flow up to and past approval. Scripted models."""
import tempfile

from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from chapters.ch18_deepagents_api import backends_tour as bt
from chapters.ch18_deepagents_api import incident_commander as ic
from chapters.ch18_deepagents_api.sim import platform
from chapters.ch18_deepagents_api.sim.scripted_model import ScriptedChatModel


def test_routes_decide_what_a_new_thread_sees():
    root, store = tempfile.mkdtemp(), InMemoryStore()
    first = bt.build_agent(ScriptedChatModel(replies=bt.thread_1_script()), root, store).invoke(
        {"messages": [{"role": "user", "content": "x"}]}, {"configurable": {"thread_id": "a"}})
    assert bt.tool_results(first)[-1].startswith("Error: Path traversal not allowed")
    second = bt.build_agent(ScriptedChatModel(replies=bt.thread_2_script()), root, store).invoke(
        {"messages": [{"role": "user", "content": "x"}]}, {"configurable": {"thread_id": "b"}})
    ls_findings, ls_workspace, ls_memories, _ = bt.tool_results(second)
    assert ls_findings == "No files found" and "report.md" in ls_workspace and "lessons.md" in ls_memories


def test_commander_delegates_writes_and_waits_for_approval():
    platform.OPS = platform.FlinkOps()
    store = InMemoryStore()
    commander, models = ic.scripted_models()
    agent = ic.build_commander(commander, models, store)
    cfg = {"configurable": {"thread_id": "t"}}
    agent.invoke({"messages": [{"role": "user", "content": ic.INCIDENT}]}, cfg)
    assert platform.OPS.restarts == [] and agent.get_state(cfg).interrupts

    out = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)
    task_results = [m.content for m in out["messages"] if m.type == "tool" and m.name == "task"]
    assert len(task_results) == 3 and task_results[2].startswith("Confirmed")        # verified-rca graph
    assert {"/findings/orders-enricher.md", "/findings/sales.orders.md", "/report.md"} <= set(out["files"])
    assert platform.OPS.restarts == ["orders-enricher@8"]
    assert [i.key for i in store.search(("oncall", "memories"))] == ["/lessons.md"]
