"""Fake Iceberg catalog: table metadata the audit checks read."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TableMeta:
    name: str
    snapshots: int
    data_files: int
    small_files: int        # data files under 8 MB


class FakeCatalog:
    """Stands in for a Polaris/Glue catalog client: the kind of handle you pass as runtime context."""

    def __init__(self, name: str):
        self.name = name
        self._tables = {t.name: t for t in [
            TableMeta("sales.orders", snapshots=1_240, data_files=9_000, small_files=3_420),
            TableMeta("sales.items", snapshots=90, data_files=1_500, small_files=60),
        ]}

    def load_table(self, table: str) -> TableMeta:
        return self._tables[table]
