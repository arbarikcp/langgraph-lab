"""Fake platform: a TaskManager log far too big for a context window, plus Iceberg table health."""
import random

ERROR = "ERROR [enrich-join] OutOfMemoryError: Java heap space (state backend: rocksdb, heap=92%)"


def taskmanager_log(job: str, lines: int = 3_000) -> str:
    """~250 KB of log: mostly noise, a few real errors."""
    rng = random.Random(42)
    out = []
    for i in range(lines):
        if i % 997 == 500:
            out.append(f"2026-11-08T03:{i % 60:02d}:00 {ERROR}")
        else:
            out.append(f"2026-11-08T03:{i % 60:02d}:00 INFO [{job}] checkpoint {i} progress "
                       f"{rng.randint(1, 99)}% records={rng.randint(10**5, 10**6)} " + "x" * 20)
    return "\n".join(out)


TABLE_HEALTH = {"sales.orders": "sales.orders: 1,240 snapshots; 38% files < 16 MB; newest data 47 min old"}
