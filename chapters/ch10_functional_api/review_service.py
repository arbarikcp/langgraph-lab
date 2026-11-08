"""Ch 10 in real life: pause for a review that takes days, resume from a *different* process.

Each command below is a separate, short-lived process, like two HTTP requests days apart.
Between them nothing is running: the paused workflow is just rows in a SQLite file.

Run:
  python -m chapters.ch10_functional_api.review_service start INC-4211      # day 1: draft, then pause
  python -m chapters.ch10_functional_api.review_service pending             # any time: what's waiting?
  python -m chapters.ch10_functional_api.review_service resume PM-INC-4211 "LGTM, add action items"
                                                                            # day 12: reviewer answers
Add --ollama to `start` to have the LLM write the draft.
In production: swap SqliteSaver for PostgresSaver, and call start/resume from your API / Slack handler.
"""
import sqlite3
import sys
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from chapters.ch10_functional_api.postmortem import build_postmortem
from chapters.ch10_functional_api.sim.incident_sources import IncidentSources

DB_PATH = Path(__file__).with_name("reviews.db")       # *.db is git-ignored


def make_workflow(use_llm: bool = False):
    """Rebuilt in every process: the code is the same, the progress comes from the database."""
    saver = SqliteSaver(sqlite3.connect(DB_PATH, check_same_thread=False))
    sources = IncidentSources()                        # fresh counters per process
    return build_postmortem(sources, use_llm, checkpointer=saver), saver, sources


def cfg_for(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def start(incident_id: str, use_llm: bool) -> None:
    workflow, _, sources = make_workflow(use_llm)
    thread_id = f"PM-{incident_id}"
    out = workflow.invoke(incident_id, cfg_for(thread_id))
    request = out["__interrupt__"][0].value             # what the reviewer must see
    print(f"paused {thread_id}. calls in this process: {dict(sources.calls)}")
    print("draft sent for review:\n  " + request["review"].replace("\n", "\n  "))
    # Real life: post `request` + `thread_id` to Slack / Jira / email here, then this process EXITS.
    print(f"\n(notify reviewer: 'reply with: resume {thread_id} <your review>') -> process exits now")


def pending() -> None:
    workflow, saver, _ = make_workflow()
    thread_ids = {c.config["configurable"]["thread_id"] for c in saver.list(None)}
    for tid in sorted(thread_ids):
        state = workflow.get_state(cfg_for(tid))
        if state.interrupts:                           # paused, waiting on a human
            print(f"WAITING  {tid}  since {state.created_at}")
        else:
            print(f"done     {tid}  url={(state.values or {}).get('url')}")


def resume(thread_id: str, review: str) -> None:
    workflow, _, sources = make_workflow()
    cfg = cfg_for(thread_id)
    state = workflow.get_state(cfg)
    if not state.interrupts:                           # guard: unknown thread, or already reviewed
        sys.exit(f"{thread_id} is not waiting for a review (next={state.next})")
    out = workflow.invoke(Command(resume=review), cfg)
    print(f"resumed {thread_id}: review={out['review']!r} url={out['url']}")
    print(f"calls in this process: {dict(sources.calls)}   <- only publish: fetch/draft came from the DB")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--ollama"]
    match args:
        case ["start", incident_id]:
            start(incident_id, use_llm="--ollama" in sys.argv)
        case ["pending"]:
            pending()
        case ["resume", thread_id, review]:
            resume(thread_id, review)
        case _:
            sys.exit(__doc__)
