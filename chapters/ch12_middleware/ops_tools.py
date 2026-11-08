"""The two on-call tools the middleware will guard: a SQL tool and a dangerous restart."""
from langchain.tools import tool

from chapters.ch12_middleware.sim import lakehouse_ops


@tool
def run_trino_query(sql: str) -> str:
    """Run a read-only Trino SQL query against the lakehouse."""
    return lakehouse_ops.OPS.query(sql)


@tool
def restart_job(job: str, parallelism: int) -> str:
    """Restart a Flink job from its latest savepoint with the given parallelism."""
    return lakehouse_ops.OPS.restart(job, parallelism)


TOOLS = [run_trino_query, restart_job]
