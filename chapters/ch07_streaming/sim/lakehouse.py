"""Fake lakehouse: Iceberg tables with manifest files to scan, plus one Flink job status."""

TABLES = {
    "sales.orders": {"manifests": 4, "finding": "3 manifests reference deleted files; 38% small files"},
    "sales.items": {"manifests": 2, "finding": "OK"},
    "sales.customers": {"manifests": 3, "finding": "OK"},
}


def list_tables(namespace: str) -> list[str]:
    return [t for t in TABLES if t.startswith(f"{namespace}.")]


def manifests(table: str) -> list[str]:
    return [f"{table}/metadata/m{i}.avro" for i in range(1, TABLES[table]["manifests"] + 1)]


def finding(table: str) -> str:
    return TABLES[table]["finding"]


def flink_job_status(job: str) -> str:
    if job == "orders-enricher":
        return "orders-enricher: lag=2,400,000 records; last checkpoint FAILED (timeout after 10m)"
    return f"{job}: lag=0; checkpoints OK"
