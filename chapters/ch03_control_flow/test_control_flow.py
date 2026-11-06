from chapters.ch03_control_flow import fleet_audit, incident_router, retry_loop
from chapters.ch03_control_flow.sim.flaky_trino import FlakyTrino


def test_command_routes_by_severity():
    graph = incident_router.build_graph()
    assert graph.invoke({"alert": "x", "severity": "P1"})["action"] == "page"
    assert graph.invoke({"alert": "x", "severity": "P3"})["action"] == "ticket"


def test_send_runs_one_branch_per_table():
    final = fleet_audit.build_graph().invoke({"namespace": "sales"})
    assert len(final["reports"]) == 3
    assert final["summary"] == "3 tables audited, 1 need maintenance"


def test_retry_loop_is_bounded():
    graph = retry_loop.build_graph()
    ok = graph.invoke({"sql": "q"}, context=retry_loop.QueryContext(FlakyTrino(2)))
    failed = graph.invoke({"sql": "q"}, context=retry_loop.QueryContext(FlakyTrino(9)))
    assert ok["attempts"] == 3 and ok["result"].startswith("rows=")
    assert failed["attempts"] == retry_loop.MAX_ATTEMPTS and failed["result"].startswith("FAILED")
