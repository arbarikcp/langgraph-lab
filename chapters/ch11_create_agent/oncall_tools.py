"""Ch 11: tools that reach the agent's world through ToolRuntime (hidden from the model)."""
import operator
from dataclasses import dataclass
from typing import Annotated

from langchain.agents import AgentState
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from chapters.ch11_create_agent.sim import flink_cluster


@dataclass
class OnCallContext:
    """Who is asking. Per run, never checkpointed (Ch 2)."""
    user_id: str
    team: str


class OnCallState(AgentState):
    """create_agent's state (messages, ...) plus our own key."""
    suspects: Annotated[list[str], operator.add]     # several flag_suspect calls can land in one step


@tool
def my_jobs(runtime: ToolRuntime[OnCallContext]) -> str:
    """List the Flink jobs owned by the caller's team. Takes no arguments."""
    return ", ".join(flink_cluster.jobs_for_team(runtime.context.team)) or "no jobs"


@tool
def flink_job_status(job: str) -> str:
    """Return consumer lag (records) and last checkpoint status for a Flink job."""
    return flink_cluster.job_status(job)


@tool
def taskmanager_metrics(job: str) -> str:
    """Return backpressure, the hottest operator and heap usage for a Flink job."""
    return flink_cluster.taskmanager_metrics(job)


@tool
def flag_suspect(job: str, reason: str, runtime: ToolRuntime[OnCallContext, OnCallState]) -> Command:
    """Record a Flink job as a suspected cause of the incident, with a one-line reason."""
    # Returning Command writes agent state, not just text. It must still answer the tool call.
    return Command(update={
        "suspects": [f"{job}: {reason} (flagged for {runtime.context.user_id})"],
        "messages": [ToolMessage(f"flagged {job}", tool_call_id=runtime.tool_call_id)],
    })


TOOLS = [my_jobs, flink_job_status, taskmanager_metrics, flag_suspect]
