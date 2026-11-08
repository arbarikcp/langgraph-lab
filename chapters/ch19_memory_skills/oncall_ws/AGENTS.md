# Data platform on-call conventions

- Severity: dashboards stale > 30 min = P2; data loss = P1.
- Never restart a Flink job without a savepoint. Restarts need human approval.
- Write raw evidence to /findings/, the incident report to /report.md.
- Save durable lessons (one line each) to /memories/lessons.md.
- Escalate to #data-platform-oncall if a fix needs a schema change.
