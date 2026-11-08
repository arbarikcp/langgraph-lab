---
name: iceberg-maintenance
description: Expire snapshots and compact small files on an Iceberg table. Use when Trino scans get slow.
---
# Iceberg maintenance runbook

1. Check snapshot count and small-file share (Trino metadata tables).
2. CALL system.expire_snapshots(table, older_than => now() - interval '7' day).
3. CALL system.rewrite_data_files(table) when > 20% of files are under 16 MB.
