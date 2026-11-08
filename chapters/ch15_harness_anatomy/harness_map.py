"""Ch 15: a small agent whose harness touches every component, with a log of what each one did.

Runs anywhere with a scripted model; add --ollama for the real LLM.
Run:  python -m chapters.ch15_harness_anatomy.harness_map [--ollama]
"""
import sys
from collections import Counter

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from chapters.ch15_harness_anatomy.harness_components import (
    ActionSpace, AuditTrail, ContextManager, LoopControl, Memory, QueryBudget, Resilience, SqlGuardrail,
    Verifier,
)
from chapters.ch15_harness_anatomy.sim import trino
from chapters.ch15_harness_anatomy.sim.scripted_model import ScriptedChatModel, call

COMPONENTS = ["loop control", "action space", "context", "memory", "guardrail", "resilience", "budget",
              "verification", "observability"]
SYSTEM_PROMPT = ("You are the lakehouse on-call assistant. Investigate with read-only Trino queries on the "
                 "Iceberg metadata tables. Answer with the root cause and the fix, citing the figures.")


@tool
def run_trino_query(sql: str) -> str:
    """Run a read-only Trino SQL query. Iceberg metadata tables: "<table>$snapshots", "$files", "$partitions"."""
    return trino.TRINO.query(sql)


def harness() -> list:
    """Order matters (Ch 12): wrap_tool_call layers run outer → inner = audit, guardrail, budget, retry."""
    return [
        Memory(),
        LoopControl(max_model_calls=8),
        ActionSpace(),
        ContextManager(keep_tool_outputs=2),
        Verifier(max_rounds=1),
        AuditTrail(),
        SqlGuardrail(),
        QueryBudget(max_queries=3),
        Resilience(retries=2),
    ]


def build_agent(model, store=None):
    return create_agent(model, tools=[run_trino_query], system_prompt=SYSTEM_PROMPT, middleware=harness(),
                        checkpointer=InMemorySaver(), store=store or InMemoryStore())


# --- demo -------------------------------------------------------------------------------------

QUESTION = "Trino is slow on sales.orders. Find out why and clean it up."
FINAL = ("Root cause: sales.orders has 1,240 snapshots (none expired in 41 days) and 38% of files are < 16 MB, "
         "so scan planning is slow. Fix: schedule expire_snapshots and rewrite_data_files.")


def first_run_script() -> list[AIMessage]:
    q = lambda sql: AIMessage("", tool_calls=[call("run_trino_query", sql=sql)])  # noqa: E731
    return [q('SELECT * FROM "sales.orders$snapshots"'),          # fails once → retried
            q("DELETE FROM sales.orders WHERE order_date < DATE '2024-01-01'"),   # blocked by policy
            q('SELECT * FROM "sales.orders$files"'),
            q('SELECT * FROM "sales.orders$partitions"'),        # over budget
            AIMessage("Probably bad partitioning."),               # rejected by the verifier
            AIMessage(FINAL)]


def second_run_script() -> list[AIMessage]:
    return [AIMessage("Same pattern as last time: 1,240 snapshots and 38% small files on sales.orders. "
                      "Run expire_snapshots and rewrite_data_files.")]


def run(agent, thread_id: str) -> tuple[dict, Counter]:
    fired: Counter = Counter()
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    for event in agent.stream({"messages": [{"role": "user", "content": QUESTION}]}, cfg, stream_mode="custom"):
        fired[event["component"]] += 1
        print(f"  [{event['component']:<13}] {event['text']}")
    return agent.get_state(cfg).values, fired


def main() -> None:
    use_ollama = "--ollama" in sys.argv
    if use_ollama:
        from common.llm import llm
    store = InMemoryStore()
    trino.TRINO = trino.FakeTrino(failures=1)

    print(f"run 1 (thread INC-1): {QUESTION}")
    model = llm if use_ollama else ScriptedChatModel(replies=first_run_script())
    state, fired = run(build_agent(model, store), "INC-1")
    print(f"\n  answer: {state['messages'][-1].content}")
    print(f"  Trino really ran: {len(trino.TRINO.queries)} queries (incl. the failed attempt); "
          f"state: {len(state['messages'])} messages, model_calls={state['model_calls']}")

    print("\nrun 2 (thread INC-2, new thread, same Store): same question")
    model = llm if use_ollama else ScriptedChatModel(replies=second_run_script())
    state, fired2 = run(build_agent(model, store), "INC-2")
    print(f"\n  answer: {state['messages'][-1].content}")

    print("\ncomponents that acted (run 1 + run 2):")
    for component in COMPONENTS:
        print(f"  {'✔' if fired[component] + fired2[component] else '·'} {component}")


if __name__ == "__main__":
    main()
