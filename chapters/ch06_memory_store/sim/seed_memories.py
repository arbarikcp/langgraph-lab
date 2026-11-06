"""What the on-call store already knows before the demo: one memory of each kind."""

# (namespace, key, value, indexed?)
SEED = [
    # semantic: facts
    (("facts", "streaming"), "orders-enricher",
     {"text": "orders-enricher is owned by streaming; pager channel #streaming-oncall"}, False),
    # procedural: rules / runbooks
    (("runbooks", "streaming"), "before-restart",
     {"text": "Take a savepoint before restarting any Flink job."}, False),
    # episodic: past incidents and how they were solved
    (("episodes", "streaming"), "INC-0990",
     {"symptom": "TaskManager OOM, heap at 98%", "resolution": "raised taskmanager.memory.process.size to 8g"}, True),
    (("episodes", "analytics"), "INC-0870",
     {"symptom": "Trino scans slow on sales.orders, many small files",
      "resolution": "ran rewrite_data_files and expire_snapshots"}, True),
]
