from chapters.ch06_memory_store.runbook_memory import OnCallContext, build_graph, make_store, thread

CLOSED = {"incident_id": "INC-1", "symptom": "checkpoint timeout, backpressure on join", "resolution": "parallelism 4->8"}
NEW = {"incident_id": "INC-2", "symptom": "checkpoints failing, join backpressured"}


def test_new_thread_recalls_fix_learned_in_another_thread():
    graph = build_graph(make_store())
    graph.invoke(CLOSED, thread("INC-1"), context=OnCallContext("streaming"))
    out = graph.invoke(NEW, thread("INC-2"), context=OnCallContext("streaming"))
    assert any("parallelism 4->8" in r for r in out["recalled"])
    assert "resolution" not in graph.get_state(thread("INC-2")).values


def test_namespaces_isolate_teams():
    graph = build_graph(make_store())
    graph.invoke(CLOSED, thread("INC-1"), context=OnCallContext("streaming"))
    out = graph.invoke(NEW, thread("INC-2"), context=OnCallContext("analytics"))
    assert out["recalled"] == [] and out["rules"] == []
