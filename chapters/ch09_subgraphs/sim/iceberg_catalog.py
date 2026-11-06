"""Fake Iceberg catalog: table health, and a record of every maintenance procedure actually run."""

TABLES = {
    "sales.orders": {"snapshots": 1_240, "small_file_pct": 38},
    "sales.items": {"snapshots": 90, "small_file_pct": 4},
}


class IcebergCatalog:
    def __init__(self):
        self.executed: list[str] = []

    def health(self, table: str) -> dict:
        return TABLES[table]

    def call(self, procedure: str) -> str:
        self.executed.append(procedure)
        return f"OK {procedure}"
