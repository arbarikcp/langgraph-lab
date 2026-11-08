"""Every harness component acts in the scripted run, and only verified answers reach long-term memory."""
from langgraph.store.memory import InMemoryStore

from chapters.ch15_harness_anatomy import harness_map as hm
from chapters.ch15_harness_anatomy.sim import trino
from chapters.ch15_harness_anatomy.sim.scripted_model import ScriptedChatModel


def test_every_component_acts_and_write_sql_never_runs():
    trino.TRINO = trino.FakeTrino(failures=1)
    state, fired = hm.run(hm.build_agent(ScriptedChatModel(replies=hm.first_run_script())), "t1")
    assert set(fired) == set(hm.COMPONENTS)
    assert not any("DELETE" in q for q in trino.TRINO.queries)
    assert state["messages"][-1].content == hm.FINAL                   # "Probably bad partitioning." was sent back


def test_memory_holds_only_the_verified_answer_and_is_recalled_on_a_new_thread():
    store = InMemoryStore()
    trino.TRINO = trino.FakeTrino()
    hm.run(hm.build_agent(ScriptedChatModel(replies=hm.first_run_script()), store), "t1")
    assert [i.value["summary"] for i in store.search(("resolutions",))] == [hm.FINAL]
    state, _ = hm.run(hm.build_agent(ScriptedChatModel(replies=hm.second_run_script()), store), "t2")
    assert state["recalled"] == [hm.FINAL]
