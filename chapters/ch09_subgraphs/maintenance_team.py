"""Ch 9: an Iceberg-maintenance team owns its own graph; the on-call graph uses it in two styles.

Run:  python -m chapters.ch09_subgraphs.maintenance_team
"""
from dataclasses import dataclass
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from chapters.ch09_subgraphs.sim.iceberg_catalog import IcebergCatalog


@dataclass
class CatalogContext:
    catalog: IcebergCatalog          # shared by parent and child: context flows into subgraphs


# ── the maintenance team's graph (child) ──────────────────────────────────────

class MaintState(TypedDict, total=False):
    table: str
    actions: list[str]
    executed: list[str]


def plan_maintenance(state: MaintState, runtime: Runtime[CatalogContext]) -> dict:
    print("   · plan_maintenance (child)")
    h = runtime.context.catalog.health(state["table"])
    actions = []
    if h["snapshots"] > 500:
        actions.append(f"CALL system.expire_snapshots('{state['table']}')")
    if h["small_file_pct"] > 20:
        actions.append(f"CALL system.rewrite_data_files('{state['table']}')")
    return {"actions": actions}


def approve_and_run(state: MaintState, runtime: Runtime[CatalogContext]) -> dict:
    if not state["actions"]:
        return {"executed": []}
    approved = interrupt({"team": "iceberg-maintenance", "table": state["table"], "actions": state["actions"]})
    if not approved:
        return {"executed": []}
    return {"executed": [runtime.context.catalog.call(a) for a in state["actions"]]}


def build_maintenance_graph():
    builder = StateGraph(MaintState, context_schema=CatalogContext)
    builder.add_node(plan_maintenance)
    builder.add_node(approve_and_run)
    builder.add_edge(START, "plan_maintenance")
    builder.add_edge("plan_maintenance", "approve_and_run")
    builder.add_edge("approve_and_run", END)
    return builder.compile()         # no checkpointer: it inherits the parent's


maintenance = build_maintenance_graph()


# ── style A: shared keys — the compiled child is the node ─────────────────────

class OnCallState(TypedDict, total=False):
    incident: str
    table: str                       # shared with MaintState
    executed: list[str]              # shared with MaintState
    report: str


def triage(state: OnCallState) -> dict:
    return {"table": "sales.orders" if "sales.orders" in state["incident"] else "sales.items"}


def write_report(state: OnCallState) -> dict:
    return {"report": f"{state['table']}: {len(state.get('executed', []))} maintenance procedures run"}


def build_shared_keys_graph():
    builder = StateGraph(OnCallState, context_schema=CatalogContext)
    builder.add_node(triage)
    builder.add_node("maintenance", maintenance)          # keys with the same name flow in and out
    builder.add_node(write_report)
    builder.add_edge(START, "triage")
    builder.add_edge("triage", "maintenance")
    builder.add_edge("maintenance", "write_report")
    builder.add_edge("write_report", END)
    return builder.compile(checkpointer=InMemorySaver())


# ── style B: different schemas — a wrapper node maps state in and out ─────────

class TicketState(TypedDict, total=False):
    ticket: str
    target_table: str                # different names: nothing is shared
    remediation: list[str]


def run_maintenance(state: TicketState) -> dict:
    print("   · run_maintenance wrapper: running from its first line")
    out = maintenance.invoke({"table": state["target_table"]})        # map in
    return {"remediation": out.get("executed", [])}                 # map out


def build_wrapper_graph():
    builder = StateGraph(TicketState, context_schema=CatalogContext)
    builder.add_node(run_maintenance)
    builder.add_edge(START, "run_maintenance")
    builder.add_edge("run_maintenance", END)
    return builder.compile(checkpointer=InMemorySaver())


# ── demo ──────────────────────────────────────────────────────────────────────

def main() -> None:
    catalog = IcebergCatalog()
    ctx = CatalogContext(catalog)

    print("▶ style A: shared keys")
    graph = build_shared_keys_graph()
    print(graph.get_graph().draw_mermaid())
    cfg = {"configurable": {"thread_id": "INC-4300"}}
    for ns, update in graph.stream({"incident": "slow Trino scans on sales.orders"}, cfg,
                                   stream_mode="updates", subgraphs=True, context=ctx):
        where = "child " if ns else "parent"
        if "__interrupt__" in update:
            update = {"__interrupt__": f"approval needed for {update['__interrupt__'][0].value['actions']}"}
        print(f"   [{where}] {update}")

    snap = graph.get_state(cfg, subgraphs=True)
    child = snap.tasks[0].state
    print(f"   paused: parent next={snap.next}, child next={child.next}, child actions={child.values['actions']}")
    final = graph.invoke(Command(resume=True), cfg, context=ctx)
    print(f"   resumed -> {final['report']}")

    print("\n▶ style B: wrapper node with its own schema")
    graph = build_wrapper_graph()
    print(graph.get_graph().draw_mermaid())
    cfg = {"configurable": {"thread_id": "TICKET-17"}}
    out = graph.invoke({"ticket": "compact sales.orders", "target_table": "sales.orders"}, cfg, context=ctx)
    print(f"   paused: interrupt from inside the child -> {out['__interrupt__'][0].value}")
    final = graph.invoke(Command(resume=True), cfg, context=ctx)
    print(f"   resumed -> remediation={final['remediation']}")

    print(f"\nprocedures the catalog actually ran (2 per style): {len(catalog.executed)}")
    for p in catalog.executed:
        print("  ", p)


if __name__ == "__main__":
    main()
