"""Ch 7: a live on-call console — LLM tokens, a tool's progress events and node updates in one stream.

Run (needs Ollama):  python -m chapters.ch07_streaming.live_console
"""
from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from chapters.ch07_streaming.sim import lakehouse
from common.llm import llm


@tool
def flink_job_status(job: str) -> str:
    """Return consumer lag (records) and last checkpoint status for a Flink job, e.g. 'orders-enricher'."""
    return lakehouse.flink_job_status(job)


@tool
def scan_manifests(table: str) -> str:
    """Scan an Iceberg table's manifest files, e.g. 'sales.orders', and report problems found."""
    write = get_stream_writer()                     # works inside tools too
    files = lakehouse.manifests(table)
    for i, name in enumerate(files, 1):
        write({"progress": f"{table}: scanned {i}/{len(files)} ({name.rsplit('/', 1)[-1]})"})
    return f"{table}: {lakehouse.finding(table)}"


TOOLS = [flink_job_status, scan_manifests]
SYSTEM = SystemMessage("You are a data-platform on-call engineer. Use the tools to check, then answer in 3 short bullets.")
model = llm.bind_tools(TOOLS)


def agent(state: MessagesState) -> dict:
    return {"messages": [model.invoke([SYSTEM] + state["messages"])]}


def build_graph():
    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")
    return builder.compile()


def main() -> None:
    graph = build_graph()
    question = HumanMessage("orders-enricher is lagging and sales.orders queries are slow. Check both.")
    print(f"you> {question.content}\n")

    parts = graph.stream({"messages": [question]}, {"recursion_limit": 12},
                         stream_mode=["messages", "custom", "updates"], version="v2")
    for part in parts:
        if part["type"] == "messages":
            token, meta = part["data"]
            if meta["langgraph_node"] == "agent" and token.content:   # skip ToolMessages and empty tool-call chunks
                print(token.content, end="", flush=True)
        elif part["type"] == "custom":
            print(f"\n   [progress] {part['data']['progress']}")
        elif part["type"] == "updates":
            for node, delta in part["data"].items():
                calls = [f"{c['name']}({c['args']})" for m in delta["messages"] for c in getattr(m, "tool_calls", [])]
                print(f"\n   [{node} done]" + (f" tool calls: {calls}" if calls else ""))
    print()


if __name__ == "__main__":
    main()
