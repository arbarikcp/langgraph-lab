---
name: flink-backpressure
description: Diagnose and fix Flink backpressure and checkpoint timeouts. Use when lag grows and checkpoints fail.
---
# Flink backpressure runbook

1. Find the first backpressured operator (taskmanager_metrics).
2. If heap > 85%: suspect state growth; check the state TTL config before scaling.
3. If a join is hot: raise its parallelism one step (x1.5); enable unaligned checkpoints.
4. Never restart without a savepoint. Restart requires human approval.
