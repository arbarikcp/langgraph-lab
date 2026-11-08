"""Fake Trino and Flink ops. Every call is recorded, so the demos can prove what really ran."""
from dataclasses import dataclass, field

TABLES = {"sales.orders": "sales.orders: 1,240 snapshots | 412 GB | 38% small files"}


@dataclass
class LakehouseOps:
    trino_failures: int = 0                 # the next N queries raise (a flaky coordinator)
    queries: list[str] = field(default_factory=list)
    restarts: list[str] = field(default_factory=list)

    def query(self, sql: str) -> str:
        self.queries.append(sql)
        if self.trino_failures > 0:
            self.trino_failures -= 1
            raise ConnectionError("Trino coordinator 503")
        hits = [row for name, row in TABLES.items() if name in sql]
        return " / ".join(hits) or "0 rows"

    def restart(self, job: str, parallelism: int) -> str:
        self.restarts.append(f"{job}@{parallelism}")
        return f"{job} restarted from latest savepoint with parallelism={parallelism}"


OPS = LakehouseOps()                         # the cluster the tools talk to; demos and tests reset it
