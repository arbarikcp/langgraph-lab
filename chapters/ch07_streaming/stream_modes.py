"""Ch 7: every stream mode over one graph (a Send fleet audit that emits progress events). No LLM.

Run:  python -m chapters.ch07_streaming.stream_modes
"""
import operator
from typing import Annotated, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from chapters.ch07_streaming.sim import lakehouse


class FleetState(TypedDict, total=False):
    namespace: str
    tables: list[str]
    reports: Annotated[list[str], operator.add]
    summary: str


class TableTask(TypedDict):
    table: str


def discover(state: FleetState) -> dict:
    return {"tables": lakehouse.list_tables(state["namespace"])}


def fan_out(state: FleetState) -> list[Send]:
    return [Send("audit_table", {"table": t}) for t in state["tables"]]


def audit_table(task: TableTask) -> dict:
    write = get_stream_writer()                     # -> the "custom" stream
    files = lakehouse.manifests(task["table"])
    for i, _ in enumerate(files, 1):
        write({"table": task["table"], "progress": f"scanned {i}/{len(files)} manifests"})
    return {"reports": [f"{task['table']}: {lakehouse.finding(task['table'])}"]}


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
    builder.add_edge("audit_table", "summarize")
    builder.add_edge("summarize", END)
    return builder.compile()


def build_parent_graph():
    """The fleet audit used as a node inside a bigger graph, to show subgraphs=True."""
    builder = StateGraph(FleetState)
    builder.add_node("fleet_audit", build_graph())
    builder.add_edge(START, "fleet_audit")
    builder.add_edge("fleet_audit", END)
    return builder.compile()


def section(title: str) -> None:
    print(f"\n━━ {title} ━━")


def main() -> None:
    graph, inputs = build_graph(), {"namespace": "sales"}

    section('stream_mode="values": full state after each step')
    for state in graph.stream(inputs, stream_mode="values"):
        print("  ", state)

    section('stream_mode="updates": only what each node returned')
    for update in graph.stream(inputs, stream_mode="updates"):
        print("  ", update)

    section('stream_mode="custom": whatever nodes write with get_stream_writer()')
    for event in graph.stream(inputs, stream_mode="custom"):
        print("  ", event)

    section('stream_mode="debug": task start / result per step (condensed)')
    for ev in graph.stream(inputs, stream_mode="debug"):
        print(f"   step {ev['step']} {ev['type']:<12} {ev['payload'].get('name')}")

    section('stream_mode=["updates", "custom"]: (mode, chunk) tuples, interleaved')
    for mode, chunk in graph.stream(inputs, stream_mode=["updates", "custom"]):
        print(f"   [{mode:<7}] {chunk}")

    section('version="v2": typed parts {type, ns, data}')
    for part in graph.stream(inputs, stream_mode=["updates", "custom"], version="v2"):
        if part["type"] == "updates":
            print(f"   {part['type']:<7} ns={part['ns']} {list(part['data'])}")
    out = graph.invoke(inputs, version="v2")
    print(f"   invoke(version='v2') -> {type(out).__name__}: value['summary']={out.value['summary']!r}")

    parent = build_parent_graph()
    section("subgraph: custom events WITHOUT subgraphs=True")
    print("  ", list(parent.stream(inputs, stream_mode="custom")), "<- nothing: inner events are hidden")

    section("subgraph: WITH subgraphs=True -> (namespace, chunk)")
    for ns, chunk in parent.stream(inputs, stream_mode="custom", subgraphs=True):
        print(f"   ns={ns[0].split(':')[0]:<12} {chunk}")


if __name__ == "__main__":
    main()
