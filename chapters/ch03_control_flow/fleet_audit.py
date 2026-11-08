"""Ch 3: Send — map-reduce over N tables, where N is only known at runtime.

Run:  python -m chapters.ch03_control_flow.fleet_audit
"""
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from chapters.ch03_control_flow.sim import fleet


class FleetState(TypedDict, total=False):
    namespace: str
    tables: list[str]
    reports: Annotated[list[str], operator.add]    # each branch appends one report
    summary: str


class TableTask(TypedDict):
    table: str


def discover(state: FleetState) -> dict:
    return {"tables": fleet.list_tables(state["namespace"])}


def fan_out(state: FleetState) -> list[Send]:
    # One Send per table: each branch runs audit_table with its own private input.
    return [Send("audit_table", {"table": t}) for t in state["tables"]]


def audit_table(task: TableTask) -> dict:          # receives the Send payload, not FleetState
    return {"reports": [f"{task['table']}: {fleet.table_health(task['table'])}"]}


def summarize(state: FleetState) -> dict:
    bad = [r for r in state["reports"] if not r.endswith(": OK")]
    return {"summary": f"{len(state['reports'])} tables audited, {len(bad)} need maintenance"}


def build_graph():
    builder = StateGraph(FleetState)
    builder.add_node(discover)
    builder.add_node(audit_table)
    builder.add_node(summarize)
    builder.add_edge(START, "discover")
    builder.add_conditional_edges("discover", fan_out, ["audit_table"])
    builder.add_edge("audit_table", "summarize")    # reduce: runs once, after every branch
    builder.add_edge("summarize", END)
    return builder.compile()


def main() -> None:
    graph = build_graph()
    print(graph.get_graph().draw_mermaid())
    for namespace in ("sales", "ops"):
        print(f"\n▶ audit namespace '{namespace}'")
        for ev in graph.stream({"namespace": namespace}, stream_mode="debug"):
            if ev["type"] == "task":
                p = ev["payload"]
                print(f"   step {ev['step']}: {p['name']:<12} input={p['input']}")
        final = graph.invoke({"namespace": namespace})
        print("   reports:", final["reports"])
        print("   summary:", final["summary"])


if __name__ == "__main__":
    main()
