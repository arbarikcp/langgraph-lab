"""Ch 2: parallel Iceberg checks whose writes merge through reducers; catalog passed as runtime context.

Run:  python -m chapters.ch02_state_reducers.table_audit
"""
import operator
from dataclasses import dataclass
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from chapters.ch02_state_reducers.sim.catalog import FakeCatalog


class AuditState(TypedDict, total=False):
    table: str
    findings: Annotated[list[str], operator.add]    # reducer: append
    files_scanned: Annotated[int, operator.add]     # reducer: sum
    verdict: str                                    # no reducer: last write wins


@dataclass
class AuditContext:
    """Per-run input. Not state: never merged, never checkpointed."""
    catalog: FakeCatalog
    max_snapshots: int = 500
    max_small_file_pct: int = 20


def check_snapshots(state: AuditState, runtime: Runtime[AuditContext]) -> dict:
    meta = runtime.context.catalog.load_table(state["table"])
    findings = []
    if meta.snapshots > runtime.context.max_snapshots:
        findings.append(f"{meta.snapshots:,} snapshots -> run expire_snapshots")
    return {"findings": findings, "files_scanned": meta.snapshots}


def check_small_files(state: AuditState, runtime: Runtime[AuditContext]) -> dict:
    meta = runtime.context.catalog.load_table(state["table"])
    pct = 100 * meta.small_files // meta.data_files
    findings = []
    if pct > runtime.context.max_small_file_pct:
        findings.append(f"{pct}% small files -> run rewrite_data_files")
    return {"findings": findings, "files_scanned": meta.data_files}


def summarize(state: AuditState) -> dict:
    return {"verdict": "NEEDS MAINTENANCE" if state["findings"] else "HEALTHY"}


def build_graph():
    builder = StateGraph(AuditState, context_schema=AuditContext)
    builder.add_node(check_snapshots)            # node name defaults to the function name
    builder.add_node(check_small_files)
    builder.add_node(summarize)
    builder.add_edge(START, "check_snapshots")   # fan-out: both checks run in super-step 1
    builder.add_edge(START, "check_small_files")
    builder.add_edge(["check_snapshots", "check_small_files"], "summarize")   # fan-in: wait for both
    builder.add_edge("summarize", END)
    return builder.compile()


def main() -> None:
    graph = build_graph()
    ctx = AuditContext(catalog=FakeCatalog("polaris-prod"))

    for table in ("sales.orders", "sales.items"):
        print(f"\n▶ audit {table} (catalog={ctx.catalog.name})")
        for ev in graph.stream({"table": table}, context=ctx, stream_mode="debug"):
            if ev["type"] == "task_result":
                print(f"   step {ev['step']}: {ev['payload']['name']:<18} -> {ev['payload']['result']}")
        final = graph.invoke({"table": table}, context=ctx)
        print("   final:", final)
        print("   'catalog' in state?", "catalog" in final)


if __name__ == "__main__":
    main()
