"""The Ch 4 agent loop, rebuilt here so this chapter stands alone. Returns the *uncompiled* builder."""
from langchain.tools import tool
from langchain_core.messages import SystemMessage
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from chapters.ch05_persistence.sim import flink_cluster
from common.llm import llm


@tool
def flink_job_status(job: str) -> str:
    """Return consumer lag (records) and last checkpoint status for a Flink job, e.g. 'orders-enricher'."""
    return flink_cluster.job_status(job)


@tool
def taskmanager_metrics(job: str) -> str:
    """Return backpressure, the hottest operator, heap usage and parallelism for a Flink job's TaskManagers."""
    return flink_cluster.taskmanager_metrics(job)


TOOLS = [flink_job_status, taskmanager_metrics]
SYSTEM = SystemMessage(
    "You are a Flink on-call engineer. Diagnose before recommending: check the job status, "
    "then the TaskManager metrics. Then answer briefly with the likely cause, the evidence and one fix."
)
model = llm.bind_tools(TOOLS)


def agent(state: MessagesState) -> dict:
    return {"messages": [model.invoke([SYSTEM] + state["messages"])]}


def agent_builder() -> StateGraph:
    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")
    return builder
