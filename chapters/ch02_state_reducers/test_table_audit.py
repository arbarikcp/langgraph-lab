from chapters.ch02_state_reducers.sim.catalog import FakeCatalog
from chapters.ch02_state_reducers.table_audit import AuditContext, build_graph

CTX = AuditContext(catalog=FakeCatalog("test"))


def test_parallel_writes_merge_through_reducers():
    final = build_graph().invoke({"table": "sales.orders"}, context=CTX)
    assert len(final["findings"]) == 2                  # appended, not overwritten
    assert final["files_scanned"] == 1_240 + 9_000      # summed
    assert final["verdict"] == "NEEDS MAINTENANCE"


def test_context_tunes_checks_without_entering_state():
    lenient = AuditContext(catalog=CTX.catalog, max_snapshots=5_000, max_small_file_pct=50)
    final = build_graph().invoke({"table": "sales.orders"}, context=lenient)
    assert final["verdict"] == "HEALTHY"
    assert "catalog" not in final
