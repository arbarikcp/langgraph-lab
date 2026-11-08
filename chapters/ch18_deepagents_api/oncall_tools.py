"""Investigation tools for the subagents, and the one production action the commander may take."""
from langchain.tools import tool

from chapters.ch18_deepagents_api.sim import platform


@tool
def flink_job_status(job: str) -> str:
    """Flink job status: lag and last checkpoint."""
    return platform.FLINK_STATUS.get(job, f"unknown job '{job}'")


@tool
def taskmanager_metrics(job: str) -> str:
    """Flink TaskManager metrics: backpressure, hot operator, heap, parallelism."""
    return platform.FLINK_METRICS.get(job, f"unknown job '{job}'")


@tool
def run_trino_query(sql: str) -> str:
    """Read-only Trino SQL. Iceberg metadata tables: "sales.orders$snapshots", "sales.orders$files"."""
    return platform.trino(sql)


@tool
def restart_job(job: str, parallelism: int) -> str:
    """Restart a Flink job from its latest savepoint with a new parallelism. Production action."""
    return platform.OPS.restart(job, parallelism)
