"""Progressive disclosure + cross-thread memory, and permission rules for parent and auditor. Scripted models."""
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from chapters.ch19_memory_skills import permissions as perms
from chapters.ch19_memory_skills import skills_memory as sm
from chapters.ch19_memory_skills.sim.scripted_model import ScriptedChatModel
from chapters.ch19_memory_skills.sim.workspace import fresh_workspace


def test_skill_bodies_load_on_demand_and_lessons_reach_the_next_thread():
    workspace, store = fresh_workspace(), InMemoryStore()
    first = ScriptedChatModel(replies=sm.thread_1_script())
    sm.run(sm.build_oncall_agent(first, workspace, store), "a", "lag")
    prompt = first.system_prompts[0]
    assert "Never restart a Flink job" in prompt and "Diagnose and fix Flink backpressure" in prompt
    assert "unaligned checkpoints" not in prompt                      # body only via read_file

    second = ScriptedChatModel(replies=sm.thread_2_script())
    sm.run(sm.build_oncall_agent(second, workspace, store), "b", "lag")
    assert sm.LESSON in second.system_prompts[0]


def test_rules_deny_hide_and_interrupt_for_parent_and_auditor():
    agent = perms.build_agent(ScriptedChatModel(replies=perms.commander_script()),
                              ScriptedChatModel(replies=perms.auditor_script()), fresh_workspace())
    cfg = {"configurable": {"thread_id": "t"}}
    events = list(agent.stream({"messages": [{"role": "user", "content": "x"}]}, cfg,
                               stream_mode="updates", subgraphs=True))
    parent = dict(perms.results(lambda ns: not ns, events))
    auditor = perms.results(lambda ns: bool(ns), events)

    assert parent["read_file"].startswith("Error: permission denied") and "/secrets/" not in parent["ls"]
    assert [text.startswith("Error: permission denied") for _, text in auditor] == [False, True, True]
    assert agent.get_state(cfg).interrupts                            # /memories/ write waits for a human
    out = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg)
    assert out["messages"][-2].content == "Updated file /memories/lessons.md"
