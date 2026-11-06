from chapters.ch01_graph_basics.triage import build_graph, classify


def test_classify_returns_partial_update():
    assert classify({"alert": "Trino coordinator DOWN"}) == {"severity": "P1"}


def test_graph_routes_by_severity():
    graph = build_graph()
    assert graph.invoke({"alert": "consumer lag 2.4M"})["route"] == "slack"
    assert graph.invoke({"alert": "compaction recommended"})["route"] == "jira"
