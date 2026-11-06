"""Ch 4: tools the model can call. The name, type hints and docstring become the schema the model sees."""
from langchain.tools import tool

from chapters.ch04_tool_loop.sim import flink_cluster


@tool
def flink_job_status(job: str) -> str:
    """Return consumer lag (records) and last checkpoint status for a Flink job, e.g. 'orders-enricher'."""
    return flink_cluster.job_status(job)


@tool
def taskmanager_metrics(job: str) -> str:
    """Return backpressure, the hottest operator, heap usage and parallelism for a Flink job's TaskManagers."""
    return flink_cluster.taskmanager_metrics(job)


TOOLS = [flink_job_status, taskmanager_metrics]
