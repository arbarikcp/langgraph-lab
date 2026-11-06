"""Fake lakehouse fleet: how many tables exist is only known when you ask the catalog."""

_TABLES = {
    "sales": {
        "sales.orders": "1,240 snapshots; 38% small files",
        "sales.items": "OK",
        "sales.customers": "OK",
    },
    "ops": {
        "ops.events": "OK",
    },
}


def list_tables(namespace: str) -> list[str]:
    return list(_TABLES.get(namespace, {}))


def table_health(table: str) -> str:
    namespace = table.split(".")[0]
    return _TABLES[namespace][table]
