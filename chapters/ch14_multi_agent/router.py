"""Ch 14, pattern 3: router (a custom graph). Classify once, Send to the matching specialists in parallel,
merge their findings. No agent decides the routing, so it is cheap, fast and predictable.

Run:  python -m chapters.ch14_multi_agent.router [--ollama]
"""
import operator
import sys
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from chapters.ch14_multi_agent.sim import scripts
from chapters.ch14_multi_agent.sim.scripted_model import ScriptedChatModel
from chapters.ch14_multi_agent.specialists import INCIDENT, build_flink_agent, build_iceberg_agent

ROUTES = {
    "flink_agent": ("flink", "lag", "checkpoint", "stale", "orders-enricher", "backpressure"),
    "iceberg_agent": ("trino", "iceberg", "snapshot", "small files", "slow", "table"),
}


class RouterState(TypedDict, total=False):
    question: str
    routes: list[str]
    findings: Annotated[list[str], operator.add]
    answer: str


class SpecialistTask(TypedDict):
    question: str


def classify(state: RouterState) -> dict:
    q = state["question"].lower()
    return {"routes": [agent for agent, words in ROUTES.items() if any(w in q for w in words)]}


def dispatch(state: RouterState) -> list[Send]:
    return [Send(agent, {"question": state["question"]}) for agent in state["routes"]] or [Send("merge", state)]


def as_node(agent, name: str):
    def run(task: SpecialistTask) -> dict:
        reply = agent.invoke({"messages": [{"role": "user", "content": task["question"]}]})["messages"][-1]
        return {"findings": [f"[{name}]\n{reply.content}"]}
    return run


def merge(state: RouterState) -> dict:
    return {"answer": "\n\n".join(state.get("findings", [])) or "No specialist matched; escalate to a human."}


def build_router(flink_model, iceberg_model):
    builder = StateGraph(RouterState)
    builder.add_node("classify", classify)
    builder.add_node("flink_agent", as_node(build_flink_agent(flink_model), "flink_agent"))
    builder.add_node("iceberg_agent", as_node(build_iceberg_agent(iceberg_model), "iceberg_agent"))
    builder.add_node("merge", merge)
    builder.add_edge(START, "classify")
    builder.add_conditional_edges("classify", dispatch, ["flink_agent", "iceberg_agent", "merge"])
    builder.add_edge("flink_agent", "merge")
    builder.add_edge("iceberg_agent", "merge")
    builder.add_edge("merge", END)
    return builder.compile()


def make_models(use_ollama: bool):
    if use_ollama:
        from common.llm import llm
        return llm, llm
    return (ScriptedChatModel(label="flink", replies=scripts.flink_replies()),
            ScriptedChatModel(label="iceberg", replies=scripts.iceberg_replies()))


def main() -> None:
    for question in (INCIDENT, "Checkpoints keep failing on orders-enricher.", "Who is on call this week?"):
        router = build_router(*make_models("--ollama" in sys.argv))      # fresh scripted replies per question
        out = router.invoke({"question": question})
        print(f"Q: {question}\n  routes={out['routes']}\n  " + out["answer"].replace("\n", "\n  ") + "\n")


if __name__ == "__main__":
    main()
