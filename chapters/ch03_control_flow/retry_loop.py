"""Ch 3: a bounded loop — a conditional edge sends the graph back until it succeeds or gives up.

Run:  python -m chapters.ch03_control_flow.retry_loop
"""
from dataclasses import dataclass
from typing import Literal, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from chapters.ch03_control_flow.sim.flaky_trino import FlakyTrino

MAX_ATTEMPTS = 3


class QueryState(TypedDict, total=False):
    sql: str
    attempts: int
    result: str
    error: str


@dataclass
class QueryContext:
    trino: FlakyTrino


def run_query(state: QueryState, runtime: Runtime[QueryContext]) -> dict:
    attempts = state.get("attempts", 0) + 1
    try:
        return {"attempts": attempts, "result": runtime.context.trino.query(state["sql"]), "error": ""}
    except TimeoutError as e:
        return {"attempts": attempts, "error": str(e)}


# Conditional edge: a pure decision, no state update. Returns the next node's name.
def should_retry(state: QueryState) -> Literal["run_query", "give_up", "__end__"]:
    if state.get("result"):
        return END
    if state["attempts"] < MAX_ATTEMPTS:
        return "run_query"                   # the loop: an edge back to an earlier node
    return "give_up"


def give_up(state: QueryState) -> dict:
    return {"result": f"FAILED after {state['attempts']} attempts: {state['error']}"}


def build_graph():
    builder = StateGraph(QueryState, context_schema=QueryContext)
    builder.add_node(run_query)
    builder.add_node(give_up)
    builder.add_edge(START, "run_query")
    builder.add_conditional_edges("run_query", should_retry)
    builder.add_edge("give_up", END)
    return builder.compile()


def main() -> None:
    graph = build_graph()
    print(graph.get_graph().draw_mermaid())
    sql = "SELECT count(*) FROM sales.orders.snapshots"

    for failures in (2, 5):
        final = graph.invoke({"sql": sql}, context=QueryContext(FlakyTrino(failures)))
        print(f"Trino fails {failures}x -> attempts={final['attempts']}  result={final['result']}")

    # recursion_limit counts super-steps; each retry is one step, so 2 is not enough here.
    # The default is 10007 in langgraph 1.2 (not 25): set it yourself if you rely on it.
    try:
        graph.invoke({"sql": sql}, context=QueryContext(FlakyTrino(2)), config={"recursion_limit": 2})
    except GraphRecursionError as e:
        print("recursion_limit=2 ->", type(e).__name__, str(e).splitlines()[0])


if __name__ == "__main__":
    main()
