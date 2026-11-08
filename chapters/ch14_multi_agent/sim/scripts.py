"""Canned replies for every agent in the scripted demos (fresh lists each call)."""
from langchain_core.messages import AIMessage

from chapters.ch14_multi_agent.sim.scripted_model import call

FLINK_FINDING = ("- orders-enricher lags 2.4M records; checkpoints time out\n"
                 "- backpressure HIGH on enrich-join, heap 92% at parallelism 4\n"
                 "- so sales.orders receives data late: dashboards are stale")
ICEBERG_FINDING = ("- sales.orders has 1,240 snapshots, no expiry for 41 days\n"
                   "- 38% small files: Trino scan planning is slow\n"
                   "- newest data is 47 min old (the writer is behind)")


def flink_replies(follow_up: bool = False) -> list[AIMessage]:
    replies = [AIMessage("", tool_calls=[call("flink_job_status", job="orders-enricher"),
                                         call("taskmanager_metrics", job="orders-enricher")]),
               AIMessage(FLINK_FINDING)]
    if follow_up:
        replies.append(AIMessage("Raise orders-enricher parallelism 4 → 8 and give enrich-join more heap."))
    return replies


def iceberg_replies() -> list[AIMessage]:
    return [AIMessage("", tool_calls=[call("table_health", table="sales.orders")]), AIMessage(ICEBERG_FINDING)]


def supervisor_replies() -> list[AIMessage]:
    return [AIMessage("", tool_calls=[
                call("ask_flink_specialist", question="Is orders-enricher healthy? It writes sales.orders."),
                call("ask_iceberg_specialist", question="Why is Trino slow on sales.orders?")]),
            AIMessage("Root cause: two problems.\n"
                      "1. orders-enricher is backpressured (enrich-join, heap 92%) → late data → stale dashboards.\n"
                      "2. sales.orders has 1,240 unexpired snapshots and 38% small files → slow Trino.\n"
                      "Fix: scale orders-enricher to parallelism 8, then expire snapshots and compact sales.orders.")]


def triage_replies() -> list[AIMessage]:
    return [AIMessage("", tool_calls=[call("transfer_to_flink", reason="stale data points at the Flink writer")])]
