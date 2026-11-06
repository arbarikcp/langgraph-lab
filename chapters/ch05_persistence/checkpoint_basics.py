"""Ch 5: checkpoints, threads, history, time-travel forks and durability, on a graph with no LLM.

Run:  python -m chapters.ch05_persistence.checkpoint_basics
"""
import operator
import sqlite3
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

DB_PATH = Path(__file__).with_name("ch05.db")


class IncidentState(TypedDict, total=False):
    hypothesis: str                                 # what we believe is wrong
    timeline: Annotated[list[str], operator.add]
    fix: str


FIXES = {"heap": "raise TaskManager memory to 8 GB", "join skew": "salt the enrich-join key"}


def detect(state: IncidentState) -> dict:
    return {"timeline": ["detect: orders-enricher lag 2.4M"]}


def diagnose(state: IncidentState) -> dict:
    return {"timeline": [f"diagnose: suspect {state.get('hypothesis', 'heap')}"]}


def mitigate(state: IncidentState) -> dict:
    return {"fix": FIXES[state.get("hypothesis", "heap")]}


def build_graph(checkpointer: BaseCheckpointSaver | None):
    builder = StateGraph(IncidentState)
    builder.add_node(detect)
    builder.add_node(diagnose)
    builder.add_node(mitigate)
    builder.add_edge(START, "detect")
    builder.add_edge("detect", "diagnose")
    builder.add_edge("diagnose", "mitigate")
    builder.add_edge("mitigate", END)
    return builder.compile(checkpointer=checkpointer)


def thread(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def short(config: dict | None) -> str:
    return config["configurable"]["checkpoint_id"][-6:] if config else "-"


def print_history(graph, config: dict) -> None:
    print(f"   {'step':>4}  {'id':<6} {'parent':<6} {'next':<14} timeline / fix")
    for snap in graph.get_state_history(config):          # newest first
        v = snap.values
        print(f"   {snap.metadata['step']:>4}  {short(snap.config):<6} {short(snap.parent_config):<6} "
              f"{str(snap.next):<14} {v.get('timeline', [])} {v.get('fix', '')}")


def main() -> None:
    saver = SqliteSaver(sqlite3.connect(DB_PATH, check_same_thread=False))
    graph = build_graph(saver)
    cfg = thread("INC-1")
    saver.delete_thread("INC-1")                 # start clean so every run prints the same thing

    print("1) run thread INC-1: one checkpoint per super-step")
    graph.invoke({}, cfg)
    print_history(graph, cfg)

    print("\n2) get_state = latest checkpoint")
    snap = graph.get_state(cfg)
    print(f"   values={snap.values}  next={snap.next}")

    print("\n3) time travel: go back to before 'diagnose', change the hypothesis, continue")
    before = next(s for s in graph.get_state_history(cfg) if s.next == ("diagnose",))
    fork_cfg = graph.update_state(before.config, {"hypothesis": "join skew"})
    print(f"   forked from {short(before.config)} -> new checkpoint {short(fork_cfg)}")
    print(f"   fork result: {graph.invoke(None, fork_cfg)['fix']}")
    print("   history now holds both branches (same parent, two children):")
    print_history(graph, cfg)

    print("\n4) durability: how many checkpoints a run writes")
    for mode in ("exit", "async", "sync"):
        tid = f"durability-{mode}"
        saver.delete_thread(tid)
        graph.invoke({}, thread(tid), durability=mode)
        print(f"   {mode:<5} -> {len(list(graph.get_state_history(thread(tid))))} checkpoints")

    print("\n5) state survives the process: a fresh connection reads the same thread")
    fresh = build_graph(SqliteSaver(sqlite3.connect(DB_PATH, check_same_thread=False)))
    print(f"   {DB_PATH.name}: INC-1 fix = {fresh.get_state(cfg).values['fix']}")


if __name__ == "__main__":
    main()
