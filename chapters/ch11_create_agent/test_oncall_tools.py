"""Tools through a ToolNode with the agent's state and context. The model is not called."""
from langchain_core.messages import AIMessage
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode

from chapters.ch11_create_agent.oncall_tools import TOOLS, OnCallContext, OnCallState, my_jobs


def run_tools(*calls: dict) -> dict:
    builder = StateGraph(OnCallState, context_schema=OnCallContext)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "tools")
    msg = AIMessage("", tool_calls=[{**c, "id": f"c{i}"} for i, c in enumerate(calls)])
    return builder.compile().invoke({"messages": [msg]}, context=OnCallContext("u42", "streaming"))


def test_runtime_is_hidden_and_context_is_injected():
    assert my_jobs.tool_call_schema.model_json_schema()["properties"] == {}
    out = run_tools({"name": "my_jobs", "args": {}})
    assert out["messages"][-1].content == "orders-enricher, orders-sink"


def test_parallel_flag_suspect_commands_accumulate_in_state():
    out = run_tools({"name": "flag_suspect", "args": {"job": "orders-enricher", "reason": "backpressure"}},
                    {"name": "flag_suspect", "args": {"job": "orders-sink", "reason": "downstream"}})
    assert len(out["suspects"]) == 2 and "u42" in out["suspects"][0]
