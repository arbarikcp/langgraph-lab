from langgraph.types import Command

from chapters.ch09_subgraphs.maintenance_team import CatalogContext, build_shared_keys_graph, build_wrapper_graph
from chapters.ch09_subgraphs.sim.iceberg_catalog import IcebergCatalog


def test_shared_keys_child_interrupt_surfaces_and_resumes():
    ctx, graph = CatalogContext(IcebergCatalog()), build_shared_keys_graph()
    cfg = {"configurable": {"thread_id": "t"}}
    paused = graph.invoke({"incident": "slow scans on sales.orders"}, cfg, context=ctx)
    assert len(paused["__interrupt__"][0].value["actions"]) == 2
    assert graph.invoke(Command(resume=True), cfg, context=ctx)["report"] == "sales.orders: 2 maintenance procedures run"
    assert len(ctx.catalog.executed) == 2


def test_wrapper_maps_schemas():
    ctx, graph = CatalogContext(IcebergCatalog()), build_wrapper_graph()
    cfg = {"configurable": {"thread_id": "t"}}
    graph.invoke({"ticket": "compact", "target_table": "sales.orders"}, cfg, context=ctx)
    final = graph.invoke(Command(resume=True), cfg, context=ctx)
    assert final["remediation"][0].startswith("OK CALL system.expire_snapshots")
