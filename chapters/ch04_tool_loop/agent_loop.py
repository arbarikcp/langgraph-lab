"""Ch 4: the hand-built agent loop — model -> tool calls? -> run tools -> back to model -> stop.

Run (needs Ollama):  python -m chapters.ch04_tool_loop.agent_loop
"""
import json

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from chapters.ch04_tool_loop.flink_tools import TOOLS
from common.llm import MODEL, llm

SYSTEM = SystemMessage(
    "You are a Flink on-call engineer. Diagnose before recommending: check the job status, "
    "then the TaskManager metrics. Then answer with the likely cause, the evidence and one fix."
)

model = llm.bind_tools(TOOLS)       # tells the model which tools exist (their schemas)


def agent(state: MessagesState) -> dict:
    # The system prompt is prepended per call, not stored in state.
    return {"messages": [model.invoke([SYSTEM] + state["messages"])]}


def build_graph():
    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(TOOLS))           # runs every tool call in the last AIMessage
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)   # tool_calls -> "tools", else END
    builder.add_edge("tools", "agent")                        # the loop
    return builder.compile()


def show_tool_schemas() -> None:
    """What bind_tools sends the model: name + docstring + argument types. Nothing else."""
    for t in TOOLS:
        print(f"{t.name}: {json.dumps(t.tool_call_schema.model_json_schema())}")


def main() -> None:
    graph = build_graph()
    print(f"model: {MODEL}\n")
    show_tool_schemas()
    question = HumanMessage("orders-enricher is lagging. Why?")
    question.pretty_print()

    # recursion_limit caps super-steps: each agent turn and each tools turn is one step.
    for update in graph.stream({"messages": [question]}, {"recursion_limit": 12}, stream_mode="updates"):
        for node, delta in update.items():
            print(f"\n──── node: {node} ────")
            for msg in delta["messages"]:
                msg.pretty_print()


if __name__ == "__main__":
    main()
