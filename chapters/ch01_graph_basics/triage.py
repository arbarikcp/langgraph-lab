"""Ch 1: deterministic alert triage as a two-node state machine (no LLM yet).

Run:  python -m chapters.ch01_graph_basics.triage
"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from chapters.ch01_graph_basics.sim.alerts import ALERTS


class TriageState(TypedDict, total=False):
    alert: str
    severity: str      # "P1" | "P2" | "P3"
    route: str         # where the alert goes


ROUTES = {"P1": "pagerduty", "P2": "slack", "P3": "jira"}


def classify(state: TriageState) -> dict:
    text = state["alert"].lower()
    if any(w in text for w in ("down", "failed")):
        sev = "P1"
    elif any(w in text for w in ("lag", "slow")):
        sev = "P2"
    else:
        sev = "P3"
    return {"severity": sev}       # partial update: only the key this node owns


def assign(state: TriageState) -> dict:
    return {"route": ROUTES[state["severity"]]}


def build_graph():
    builder = StateGraph(TriageState)
    builder.add_node("classify", classify)
    builder.add_node("assign", assign)
    builder.add_edge(START, "classify")
    builder.add_edge("classify", "assign")
    builder.add_edge("assign", END)
    return builder.compile()       # validates wiring, returns a Runnable


def main() -> None:
    graph = build_graph()
    print("Graph:\n")
    print(graph.get_graph().draw_ascii())   # box-drawing render (needs grandalf)

    for alert in ALERTS:
        print(f"\n▶ {alert}")
        # "debug" mode exposes the Pregel super-step each node ran in.
        for ev in graph.stream({"alert": alert}, stream_mode="debug"):
            if ev["type"] == "task_result":
                print(f"   step {ev['step']}: {ev['payload']['name']:<8} -> {ev['payload']['result']}")
        print("   final:", graph.invoke({"alert": alert}))


if __name__ == "__main__":
    main()
