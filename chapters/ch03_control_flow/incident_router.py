"""Ch 3: Command — a node updates state AND picks the next node in one return.

Run:  python -m chapters.ch03_control_flow.incident_router
"""
import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from chapters.ch03_control_flow.sim.incidents import INCIDENTS


class Incident(TypedDict, total=False):
    alert: str
    severity: str
    action: str
    log: Annotated[list[str], operator.add]


# The Literal lists the possible targets, so compile() and the drawing know the edges.
def triage(state: Incident) -> Command[Literal["page_oncall", "open_ticket"]]:
    if state["severity"] == "P1":
        return Command(update={"action": "page"}, goto="page_oncall")
    return Command(update={"action": "ticket"}, goto="open_ticket")


def page_oncall(state: Incident) -> dict:
    return {"log": [f"PAGED streaming on-call: {state['alert']}"]}


def open_ticket(state: Incident) -> dict:
    return {"log": [f"JIRA ticket opened: {state['alert']}"]}


def build_graph():
    builder = StateGraph(Incident)
    builder.add_node(triage)
    builder.add_node(page_oncall)
    builder.add_node(open_ticket)
    builder.add_edge(START, "triage")       # no edge out of triage: its Command routes
    builder.add_edge("page_oncall", END)
    builder.add_edge("open_ticket", END)
    return builder.compile()


def main() -> None:
    graph = build_graph()
    print(graph.get_graph().draw_mermaid())
    for incident in INCIDENTS:
        final = graph.invoke(incident)
        print(f"{final['severity']} action={final['action']:<6} {final['log']}")


if __name__ == "__main__":
    main()
