"""Deterministic parts of the loop only: tools, ToolNode and the router. The model is not called."""
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from chapters.ch04_tool_loop.flink_tools import TOOLS

CALLS = AIMessage("", tool_calls=[
    {"name": "flink_job_status", "args": {"job": "orders-enricher"}, "id": "c1"},
    {"name": "taskmanager_metrics", "args": {"job": "orders-enricher"}, "id": "c2"},
])


def test_tools_condition_routes_on_tool_calls():
    assert tools_condition({"messages": [CALLS]}) == "tools"
    assert tools_condition({"messages": [AIMessage("Root cause: ...")]}) == END


def test_tool_node_answers_every_call():
    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "tools")
    replies = builder.compile().invoke({"messages": [CALLS]})["messages"][1:]
    assert [m.tool_call_id for m in replies] == ["c1", "c2"]
    assert "FAILED" in replies[0].content and "enrich-join" in replies[1].content
