"""Fake Flink operations API. Counts every side effect so you can see what ran, and how often."""


class FlinkOps:
    def __init__(self):
        self.restarts: list[str] = []
        self.pages: list[str] = []

    def metrics(self, job: str) -> dict:
        return {"job": job, "parallelism": 4, "backpressure": "HIGH", "hot_operator": "enrich-join"}

    def restart(self, job: str, parallelism: int) -> str:
        self.restarts.append(f"{job}@{parallelism}")
        return f"{job} restarted from latest savepoint with parallelism {parallelism}"

    def page(self, message: str) -> None:
        self.pages.append(message)
