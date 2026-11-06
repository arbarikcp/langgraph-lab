"""Alerts the on-call pager receives during the orders-enricher incident."""

ALERTS = [
    "Trino coordinator DOWN in prod",
    "Flink orders-enricher checkpoint FAILED (timeout after 10m)",
    "Flink orders-enricher consumer lag 2.4M records",
    "Iceberg sales.orders: 38% small files, compaction recommended",
]
