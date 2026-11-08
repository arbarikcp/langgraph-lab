"""A compact verified-RCA graph, rebuilt here in the style of Ch 16, to plug into a deep agent
as a CompiledSubAgent. act ⇄ tools → verify → (act with feedback | END), with a step budget in state.

Any compiled graph with a `messages` key can be a subagent: the parent sends the task as a HumanMessage
and gets back the content of the last message.
"""
import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from chapters.ch18_deepagents_api.oncall_tools import flink_job_status, run_trino_query, taskmanager_metrics

TOOLS = [flink_job_status, taskmanager_metrics, run_trino_query]
MAX_STEPS, MAX_ROUNDS = 8, 2
SYSTEM = SystemMessage("Find the root cause with the tools. Cite figures from the tool results "
                       "and give one concrete next action.")
NUMBER = re.compile(r"\d[\d,.]*%?")


class RCAState(MessagesState):
    steps: int
    rounds: int


def build_rca_graph(model):
    def act(state: RCAState) -> Command[Literal["tools", "verify"]]:
        forced = state.get("steps", 0) >= MAX_STEPS
        prompt = [SYSTEM, *state["messages"]] + ([HumanMessage("Budget exhausted. Answer now.")] if forced else [])
        msg = (model if forced else model.bind_tools(TOOLS)).invoke(prompt)
        if forced:
            msg = msg.model_copy(update={"tool_calls": []})
        return Command(update={"messages": [msg], "steps": state.get("steps", 0) + 1},
                       goto="tools" if msg.tool_calls else "verify")

    def verify(state: RCAState) -> Command[Literal["act", "__end__"]]:
        answer = state["messages"][-1].content
        evidence = " ".join(m.content for m in state["messages"] if m.type == "tool")
        rounds = state.get("rounds", 0) + 1
        if any(n in evidence for n in NUMBER.findall(answer)) or rounds >= MAX_ROUNDS:
            return Command(update={"rounds": rounds}, goto=END)
        return Command(update={"rounds": rounds,
                               "messages": [HumanMessage("Verifier: cite figures from the tool results.")]},
                       goto="act")

    builder = StateGraph(RCAState)
    builder.add_node("act", act)
    builder.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))
    builder.add_node("verify", verify)
    builder.add_edge(START, "act")
    builder.add_edge("tools", "act")
    return builder.compile(name="verified-rca")
