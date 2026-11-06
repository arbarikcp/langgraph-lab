from chapters.ch07_streaming.stream_modes import build_graph, build_parent_graph

INPUTS = {"namespace": "sales"}


def test_custom_stream_gets_one_event_per_manifest():
    events = list(build_graph().stream(INPUTS, stream_mode="custom"))
    assert len(events) == 4 + 2 + 3                     # manifests in sales.orders, items, customers


def test_subgraph_events_need_subgraphs_true():
    parent = build_parent_graph()
    assert list(parent.stream(INPUTS, stream_mode="custom")) == []
    assert len(list(parent.stream(INPUTS, stream_mode="custom", subgraphs=True))) == 9
