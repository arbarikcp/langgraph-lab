"""Two investigation tools: one returns a huge log (to force offload), one a small table report."""
from langchain.tools import tool

from chapters.ch17_deep_agents.sim import platform


@tool
def taskmanager_logs(job: str) -> str:
    """Full TaskManager logs for a Flink job. Very large."""
    return platform.taskmanager_log(job)


@tool
def table_health(table: str) -> str:
    """Iceberg table health: snapshots, small files, data freshness."""
    return platform.TABLE_HEALTH.get(table, f"unknown table '{table}'")
