"""Fake platform for the 'orders-enricher lags, dashboards stale' incident.

Trino fails on every Nth query (exercise 1); the catalog counts metadata loads (to show the cache).
"""
from dataclasses import dataclass, field

FLINK_STATUS = {"orders-enricher": "orders-enricher: lag=2,400,000 records; last checkpoint FAILED "
                                   "(timeout after 10m); writes sales.orders"}
FLINK_METRICS = {"orders-enricher": "orders-enricher: backpressure=HIGH on 'enrich-join'; heap=92%; parallelism=4"}
TRINO_ROWS = {
    "$snapshots": "sales.orders$snapshots: newest snapshot 47 min old; 1,240 snapshots",
    "$files": "sales.orders$files: 38% of 9,800 files < 16 MB",
}


@dataclass
class FakeTrino:
    fail_every: int = 0                  # 2 → every 2nd query raises
    queries: list[str] = field(default_factory=list)

    def query(self, sql: str) -> str:
        self.queries.append(sql)
        if self.fail_every and len(self.queries) % self.fail_every == 0:
            raise ConnectionError("Trino coordinator 503")
        return next((rows for key, rows in TRINO_ROWS.items() if key in sql), "0 rows")


@dataclass
class FakeCatalog:
    loads: int = 0

    def describe(self, table: str) -> str:
        self.loads += 1                  # an expensive metadata scan in real life
        return f"{table}: Iceberg v2, partitioned by day(order_ts), written by orders-enricher"


TRINO = FakeTrino()
CATALOG = FakeCatalog()
