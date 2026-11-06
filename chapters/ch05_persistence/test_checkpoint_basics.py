from langgraph.checkpoint.memory import InMemorySaver

from chapters.ch05_persistence.checkpoint_basics import build_graph, thread


def test_fork_changes_outcome_and_keeps_original():
    graph, cfg = build_graph(InMemorySaver()), thread("t")
    graph.invoke({}, cfg)
    before = next(s for s in graph.get_state_history(cfg) if s.next == ("diagnose",))
    fork = graph.update_state(before.config, {"hypothesis": "join skew"})
    assert graph.invoke(None, fork)["fix"] == "salt the enrich-join key"
    fixes = {s.values.get("fix") for s in graph.get_state_history(cfg)}
    assert "raise TaskManager memory to 8 GB" in fixes          # original branch still there


def test_durability_exit_keeps_only_final_checkpoint():
    graph = build_graph(InMemorySaver())
    graph.invoke({}, thread("exit"), durability="exit")
    graph.invoke({}, thread("async"))
    assert len(list(graph.get_state_history(thread("exit")))) == 1
    assert len(list(graph.get_state_history(thread("async")))) == 5
