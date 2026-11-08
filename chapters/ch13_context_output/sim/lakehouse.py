"""Fake lakehouse for the Trino-slowdown incident: team ownership, table health, Flink jobs, restarts."""
from dataclasses import dataclass, field

TEAM_JOBS = {"streaming": ["orders-enricher", "orders-sink"], "analytics": []}
TEAM_TABLES = {"streaming": ["sales.orders"], "analytics": ["sales.orders", "sales.daily_revenue"]}

TABLE_HEALTH = {
    "sales.orders": "sales.orders: 1,240 snapshots (expire_snapshots last ran 41 days ago); "
                    "412 GB; 38% files < 16 MB; scan planning 18s (was 3s last week)",
    "sales.daily_revenue": "sales.daily_revenue: 52 snapshots; 3 GB; 2% small files; healthy",
}

JOB_STATUS = {
    "orders-enricher": "orders-enricher: lag=2,400,000 records; last checkpoint FAILED (timeout after 10m)",
    "orders-sink": "orders-sink: lag=0; commits every 30s to sales.orders (1 snapshot per commit)",
}


@dataclass
class Lakehouse:
    restarts: list[str] = field(default_factory=list)

    def restart(self, job: str, parallelism: int) -> str:
        self.restarts.append(f"{job}@{parallelism}")
        return f"{job} restarted with parallelism={parallelism}"


LAKEHOUSE = Lakehouse()
