"""Fake Trino over Iceberg metadata tables for the slow-sales.orders incident. Can fail N times; records queries."""
from dataclasses import dataclass, field

METADATA = {
    "$snapshots": "sales.orders$snapshots: 1,240 snapshots; oldest 41 days; expire_snapshots never scheduled",
    "$files": "sales.orders$files: 9,800 data files; 38% < 16 MB",
    "$partitions": "sales.orders$partitions: 730 daily partitions; largest 2.1 GB",
}


@dataclass
class FakeTrino:
    failures: int = 0                         # the next N queries raise (coordinator restarting)
    queries: list[str] = field(default_factory=list)

    def query(self, sql: str) -> str:
        self.queries.append(sql)
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("Trino coordinator 503")
        return next((rows for key, rows in METADATA.items() if key in sql), "0 rows")


TRINO = FakeTrino()
