"""Ch 5: the agent loop with a SqliteSaver — multi-turn memory on one thread, then a time-travel fork.

Run (needs Ollama):  python -m chapters.ch05_persistence.incident_thread
"""
import sqlite3
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from chapters.ch05_persistence.agent_builder import agent_builder

DB_PATH = Path(__file__).with_name("ch05.db")
THREAD_ID = "INC-4211"                  # one incident = one thread


def ask(graph, config: dict, text: str | None) -> str:
    """Run one turn (text=None continues from the checkpoint in config) and return the final answer."""
    payload = {"messages": [HumanMessage(text)]} if text else None
    out = graph.invoke(payload, {**config, "recursion_limit": 12})
    return out["messages"][-1].content


def main() -> None:
    saver = SqliteSaver(sqlite3.connect(DB_PATH, check_same_thread=False))
    saver.delete_thread(THREAD_ID)      # start clean so the demo is repeatable
    graph = agent_builder().compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": THREAD_ID}}

    print("── turn 1 ──")
    print(ask(graph, cfg, "orders-enricher is lagging. Why?"))

    print("\n── turn 2 (same thread: the model sees turn 1) ──")
    print(ask(graph, cfg, "Summarise what you found in 2 lines."))

    snap = graph.get_state(cfg)
    history = list(graph.get_state_history(cfg))
    print(f"\nthread {THREAD_ID}: {len(snap.values['messages'])} messages, {len(history)} checkpoints")

    print("\n── fork: back to just after the first question, add an assumption ──")
    first_question = next(s for s in reversed(history)
                          if s.next == ("agent",) and len(s.values["messages"]) == 1)
    fork_cfg = graph.update_state(
        first_question.config,
        {"messages": [HumanMessage("Assume heap is fine; focus on the join operator.")]},
    )
    print(ask(graph, fork_cfg, None))

    original = history[0].values["messages"][-1].content      # newest checkpoint before the fork
    print(f"\noriginal branch untouched, its last answer still starts: {original[:80]!r}")


if __name__ == "__main__":
    main()
