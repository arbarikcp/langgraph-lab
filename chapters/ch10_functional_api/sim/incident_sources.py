"""Fake incident systems (pager history, metrics, wiki). Counts every call; the wiki can be down."""
from collections import Counter


class IncidentSources:
    def __init__(self, wiki_failures: int = 0):
        self.calls: Counter[str] = Counter()
        self.wiki_failures = wiki_failures        # publish() fails this many times first
        self.pages_published: list[str] = []

    def timeline(self, incident_id: str) -> str:
        self.calls["timeline"] += 1
        return "14:02 lag alert; 14:09 checkpoint FAILED; 14:30 restart with parallelism 8; 14:41 recovered"

    def metrics(self, job: str) -> str:
        self.calls[f"metrics:{job}"] += 1
        peaks = {"orders-enricher": "peak lag 2.4M, backpressure HIGH for 28m, heap 92%",
                 "orders-sink": "no lag, healthy"}
        return f"{job}: {peaks.get(job, 'no data')}"

    def publish(self, incident_id: str, doc: str) -> str:
        self.calls["publish"] += 1
        if self.wiki_failures > 0:
            self.wiki_failures -= 1
            raise ConnectionError("wiki returned 503")
        self.pages_published.append(incident_id)
        return f"https://wiki.internal/postmortems/{incident_id}"
