"""Fake Flink cluster with team ownership: what the Flink REST API would report during the incident."""
from dataclasses import dataclass


@dataclass(frozen=True)
class FlinkJob:
    name: str
    team: str
    lag_records: int
    last_checkpoint: str
    backpressure: str
    hot_operator: str | None
    heap_pct: int


JOBS = {j.name: j for j in [
    FlinkJob("orders-enricher", "streaming", 2_400_000, "FAILED (timeout after 10m)", "HIGH", "enrich-join", 92),
    FlinkJob("orders-sink", "streaming", 0, "OK", "LOW", None, 41),
    FlinkJob("feature-writer", "ml", 1_200, "OK", "LOW", None, 55),
]}


def jobs_for_team(team: str) -> list[str]:
    return [j.name for j in JOBS.values() if j.team == team]


def job_status(job: str) -> str:
    j = JOBS.get(job)
    if j is None:
        return f"unknown job '{job}'"
    return f"{j.name}: lag={j.lag_records:,} records; last checkpoint {j.last_checkpoint}"


def taskmanager_metrics(job: str) -> str:
    j = JOBS.get(job)
    if j is None:
        return f"unknown job '{job}'"
    hot = f" on operator '{j.hot_operator}'" if j.hot_operator else ""
    return f"{j.name}: backpressure={j.backpressure}{hot}; heap={j.heap_pct}%"
