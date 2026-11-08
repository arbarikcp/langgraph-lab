"""Fake platform for INC-4211: dashboards stale, orders-enricher lagging, sales.orders slow. Records restarts."""
from dataclasses import dataclass, field

FLINK_STATUS = {"orders-enricher": "orders-enricher: lag=2,400,000 records; last checkpoint FAILED (timeout after 10m)"}
FLINK_METRICS = {"orders-enricher": "orders-enricher: backpressure=HIGH on 'enrich-join'; heap=92%; parallelism=4"}
TRINO_ROWS = {"$snapshots": "sales.orders$snapshots: 1,240 snapshots; newest 47 min old",
              "$files": "sales.orders$files: 38% of 9,800 files < 16 MB"}


@dataclass
class FlinkOps:
    restarts: list[str] = field(default_factory=list)

    def restart(self, job: str, parallelism: int) -> str:
        self.restarts.append(f"{job}@{parallelism}")
        return f"{job} restarted from savepoint with parallelism={parallelism}"


def trino(sql: str) -> str:
    return next((rows for key, rows in TRINO_ROWS.items() if key in sql), "0 rows")


OPS = FlinkOps()
