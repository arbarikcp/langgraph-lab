"""Fake data platform during the incident: dashboards stale + Trino slow on sales.orders."""

FLINK_STATUS = {
    "orders-enricher": "orders-enricher: lag=2,400,000 records; last checkpoint FAILED (timeout after 10m); "
                       "writes sales.orders",
}
FLINK_METRICS = {
    "orders-enricher": "orders-enricher: backpressure=HIGH on operator 'enrich-join'; heap=92%; parallelism=4",
}
TABLE_HEALTH = {
    "sales.orders": "sales.orders: 1,240 snapshots (expire_snapshots last ran 41 days ago); 412 GB; "
                    "38% files < 16 MB; newest data 47 min old",
}


def flink_job_status(job: str) -> str:
    return FLINK_STATUS.get(job, f"unknown job '{job}'")


def taskmanager_metrics(job: str) -> str:
    return FLINK_METRICS.get(job, f"unknown job '{job}'")


def table_health(table: str) -> str:
    return TABLE_HEALTH.get(table, f"unknown table '{table}'")
