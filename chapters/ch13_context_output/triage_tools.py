"""Triage tools and the caller context they read. Which of them the model sees is decided per call."""
from dataclasses import dataclass

from langchain.tools import ToolRuntime, tool

from chapters.ch13_context_output.sim import lakehouse


@dataclass
class TriageContext:
    user_id: str
    team: str


@tool
def my_tables(runtime: ToolRuntime[TriageContext]) -> str:
    """List the Iceberg tables owned or used by the caller's team."""
    return ", ".join(lakehouse.TEAM_TABLES.get(runtime.context.team, []))


@tool
def table_health(table: str) -> str:
    """Iceberg table health: snapshots, size, small files, scan planning time."""
    return lakehouse.TABLE_HEALTH.get(table, f"unknown table '{table}'")


@tool
def flink_job_status(job: str) -> str:
    """Status of a Flink streaming job: lag, checkpoints, commit behaviour."""
    return lakehouse.JOB_STATUS.get(job, f"unknown job '{job}'")


@tool
def restart_job(job: str, parallelism: int) -> str:
    """Restart a Flink job from its latest savepoint with the given parallelism."""
    return lakehouse.LAKEHOUSE.restart(job, parallelism)


TOOLS = [my_tables, table_health, flink_job_status, restart_job]
TEAM_TOOLS = {                       # least privilege: what each team's on-call may use
    "streaming": {"my_tables", "table_health", "flink_job_status", "restart_job"},
    "analytics": {"my_tables", "table_health"},
}
