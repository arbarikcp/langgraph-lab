"""Two specialist agents with narrow toolsets. Every pattern in this chapter reuses them."""
from langchain.agents import create_agent
from langchain.tools import tool

from chapters.ch14_multi_agent.sim import platform

INCIDENT = ("Dashboards are stale and Trino is slow on sales.orders. "
            "orders-enricher writes that table.")


@tool
def flink_job_status(job: str) -> str:
    """Flink job status: lag, last checkpoint, which table it writes."""
    return platform.flink_job_status(job)


@tool
def taskmanager_metrics(job: str) -> str:
    """Flink TaskManager metrics: backpressure, hot operator, heap, parallelism."""
    return platform.taskmanager_metrics(job)


@tool
def table_health(table: str) -> str:
    """Iceberg table health: snapshots, size, small files, data freshness."""
    return platform.table_health(table)


FLINK_PROMPT = "Flink specialist. Diagnose lag, checkpoints and backpressure with your tools. Reply in <=3 bullets."
ICEBERG_PROMPT = "Iceberg/Trino specialist. Diagnose snapshots, small files and freshness. Reply in <=3 bullets."


def build_flink_agent(model, **kwargs):
    return create_agent(model, tools=[flink_job_status, taskmanager_metrics], system_prompt=FLINK_PROMPT,
                        name="flink_agent", **kwargs)


def build_iceberg_agent(model, **kwargs):
    return create_agent(model, tools=[table_health], system_prompt=ICEBERG_PROMPT,
                        name="iceberg_agent", **kwargs)
