"""Fake Flink cluster: what the Flink REST API would report during the incident."""
from dataclasses import dataclass


@dataclass(frozen=True)
class FlinkJob:
    name: str
    lag_records: int
    last_checkpoint: str        # "OK" or a failure reason
    backpressure: str           # "LOW" | "HIGH"
    hot_operator: str | None
    heap_pct: int
    parallelism: int


JOBS = {j.name: j for j in [
    FlinkJob("orders-enricher", 2_400_000, "FAILED (timeout after 10m)", "HIGH", "enrich-join", 92, 4),
    FlinkJob("orders-sink", 0, "OK", "LOW", None, 41, 2),
]}


def job_status(job: str) -> str:
    j = JOBS.get(job)
    if j is None:
        return f"unknown job '{job}'. Known jobs: {', '.join(JOBS)}"
    return f"{j.name}: lag={j.lag_records:,} records; last checkpoint {j.last_checkpoint}"


def taskmanager_metrics(job: str) -> str:
    j = JOBS.get(job)
    if j is None:
        return f"unknown job '{job}'. Known jobs: {', '.join(JOBS)}"
    hot = f" on operator '{j.hot_operator}'" if j.hot_operator else ""
    return f"{j.name}: backpressure={j.backpressure}{hot}; heap={j.heap_pct}%; parallelism={j.parallelism}"
