"""The investigation tools. Trino is flaky (sim), so the harness must contain its errors."""
from langchain.tools import tool

from chapters.ch16_custom_harness.sim import platform


@tool
def flink_job_status(job: str) -> str:
    """Flink job status: lag, last checkpoint, which table it writes."""
    return platform.FLINK_STATUS.get(job, f"unknown job '{job}'")


@tool
def taskmanager_metrics(job: str) -> str:
    """Flink TaskManager metrics: backpressure, hot operator, heap, parallelism."""
    return platform.FLINK_METRICS.get(job, f"unknown job '{job}'")


@tool
def run_trino_query(sql: str) -> str:
    """Read-only Trino SQL. Iceberg metadata tables: "sales.orders$snapshots", "sales.orders$files"."""
    return platform.TRINO.query(sql)


TOOLS = [flink_job_status, taskmanager_metrics, run_trino_query]
