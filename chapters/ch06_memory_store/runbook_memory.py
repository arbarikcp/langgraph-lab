"""Ch 6: long-term memory with a Store — INC-4300 recalls how INC-4211 was fixed, across threads.

Run:  python -m chapters.ch06_memory_store.runbook_memory            # keyword embeddings, runs anywhere
      python -m chapters.ch06_memory_store.runbook_memory --ollama   # real nomic-embed-text embeddings
"""
import sys
from dataclasses import dataclass
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from chapters.ch06_memory_store.sim.keyword_embeddings import DIMS, KeywordEmbeddings
from chapters.ch06_memory_store.sim.seed_memories import SEED


@dataclass
class OnCallContext:
    team: str
    min_score: float = 0.5          # ignore weak matches


class IncidentState(TypedDict, total=False):
    incident_id: str
    symptom: str
    resolution: str                 # set when the incident is closed
    recalled: list[str]             # past fixes that look similar
    rules: list[str]                # team runbook rules


def recall(state: IncidentState, runtime: Runtime[OnCallContext]) -> dict:
    team, store = runtime.context.team, runtime.store
    hits = store.search(("episodes", team), query=state["symptom"], limit=3)    # semantic search
    recalled = [f"{h.key} ({h.score:.2f}): {h.value['resolution']}"
                for h in hits if h.score is not None and h.score >= runtime.context.min_score]
    rules = [h.value["text"] for h in store.search(("runbooks", team))]          # list, no query
    return {"recalled": recalled, "rules": rules}


def learn(state: IncidentState, runtime: Runtime[OnCallContext]) -> dict:
    if state.get("resolution"):
        runtime.store.put(("episodes", runtime.context.team), state["incident_id"],
                          {"symptom": state["symptom"], "resolution": state["resolution"]})
    return {}


def make_store(use_ollama: bool = False) -> InMemoryStore:
    if use_ollama:
        from common.llm import EMBED_DIMS, embeddings
        index = {"embed": embeddings, "dims": EMBED_DIMS, "fields": ["symptom"]}
    else:
        index = {"embed": KeywordEmbeddings(), "dims": DIMS, "fields": ["symptom"]}
    store = InMemoryStore(index=index)              # only the "symptom" field is embedded
    for namespace, key, value, indexed in SEED:
        store.put(namespace, key, value, index=None if indexed else False)
    return store


def build_graph(store: BaseStore):
    builder = StateGraph(IncidentState, context_schema=OnCallContext)
    builder.add_node(recall)
    builder.add_node(learn)
    builder.add_edge(START, "recall")
    builder.add_edge("recall", "learn")
    builder.add_edge("learn", END)
    # checkpointer = short-term (per thread); store = long-term (shared by every thread)
    return builder.compile(checkpointer=InMemorySaver(), store=store)


def thread(incident_id: str) -> dict:
    return {"configurable": {"thread_id": incident_id}}


def main() -> None:
    store = make_store(use_ollama="--ollama" in sys.argv)
    graph = build_graph(store)
    streaming = OnCallContext(team="streaming")

    print("1) namespaces in the store")
    for ns in store.list_namespaces():
        print("  ", ns, [item.key for item in store.search(ns)])

    print("\n2) INC-4211 is closed with a resolution -> learn() stores the episode")
    graph.invoke({"incident_id": "INC-4211",
                  "symptom": "checkpoint timeout with high backpressure on join",
                  "resolution": "raised enrich-join parallelism 4->8, enabled unaligned checkpoints"},
                 thread("INC-4211"), context=streaming)
    print("   stored:", store.get(("episodes", "streaming"), "INC-4211").value)

    print("\n3) INC-4300 (new thread, new wording) -> recall() finds INC-4211's fix")
    new = {"incident_id": "INC-4300", "symptom": "checkpoints failing, join operator backpressured"}
    ranked = store.search(("episodes", "streaming"), query=new["symptom"])
    print("   raw ranking:", [(h.key, round(h.score, 2)) for h in ranked], f"(min_score={streaming.min_score})")
    out = graph.invoke(new, thread("INC-4300"), context=streaming)
    print("   recalled:", out["recalled"])
    print("   rules:   ", out["rules"])

    print("\n4) same symptom, team 'analytics' -> streaming memories are invisible")
    out = graph.invoke(new, thread("INC-4300-analytics"), context=OnCallContext(team="analytics"))
    print("   recalled:", out["recalled"], " rules:", out["rules"])

    print("\n5) short-term (checkpointer, per thread) vs long-term (store, shared)")
    for tid in ("INC-4211", "INC-4300"):
        print(f"   thread {tid} state keys:", sorted(graph.get_state(thread(tid)).values))
    print("   INC-4300 never saw INC-4211's thread; the fix reached it only through the store.")


if __name__ == "__main__":
    main()
