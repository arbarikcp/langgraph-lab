# Ch 6 explained: long-term memory with a `Store`

A guided walk through the theory and the code. Every output shown here was produced by running the code (langgraph 1.2.20).

---

## 1. The problem this chapter solves

Two incidents, a few days apart:

| | INC-4211 (last week) | INC-4300 (today) |
| --- | --- | --- |
| Symptom | "checkpoint timeout with high backpressure on join" | "checkpoints failing, join operator backpressured" |
| Status | **Fixed**: "raised enrich-join parallelism 4->8, enabled unaligned checkpoints" | Open. What should we do? |

They're the same problem, worded differently. We want INC-4300 to **remember** how INC-4211 was fixed.

In Ch 5, each incident runs on its own **thread** (`thread_id = "INC-4211"`, `"INC-4300"`), and the checkpointer saves that thread's state. But a thread can only see **its own** history. INC-4300's thread has never heard of INC-4211. So we need a second kind of memory that **every thread shares**. That's the `Store`.

---

## 2. Two kinds of memory

| | Short-term memory | Long-term memory |
| --- | --- | --- |
| LangGraph piece | **Checkpointer** (`InMemorySaver`, `SqliteSaver`, Ch 5) | **Store** (`InMemoryStore`, Postgres store, …) |
| Scope | **One thread** | **All threads** (and all graphs using that store) |
| What's in it | The graph's state, saved every super-step | JSON documents you choose to save |
| Who writes it | LangGraph, **automatically** | **You**, explicitly with `store.put(...)` |
| How you read it | Automatically loaded when you invoke the same `thread_id` | Explicitly with `store.get(...)` / `store.search(...)` |
| Analogy | Your notebook for this one incident | The team's wiki / runbook folder |

```python
builder.compile(checkpointer=InMemorySaver(),   # short-term: per thread
                store=store)                     # long-term: shared
```

Step 5 of the demo proves the separation:

```text
thread INC-4211 state keys: ['incident_id', 'recalled', 'resolution', 'rules', 'symptom']
thread INC-4300 state keys: ['incident_id', 'recalled', 'rules', 'symptom']     ← no 'resolution'
```

INC-4300's own state never contained INC-4211's data. The fix reached it **only** through the store.

---

## 3. How the Store organises data: namespace + key + value

Think of it as a **file system of JSON files**:

| Store concept | File-system analogy | Example |
| --- | --- | --- |
| **namespace** (a tuple) | Folder path | `("episodes", "streaming")` ≈ `episodes/streaming/` |
| **key** (a string) | File name | `"INC-4211"` |
| **value** (a dict) | File contents (JSON) | `{"symptom": "...", "resolution": "..."}` |

What the store holds after the demo:

```text
store
├── facts/streaming/orders-enricher      {"text": "orders-enricher is owned by streaming; pager ..."}
├── runbooks/streaming/before-restart    {"text": "Take a savepoint before restarting any Flink job."}
└── episodes/
    ├── streaming/INC-0990               {"symptom": "TaskManager OOM, heap at 98%", "resolution": "..."}
    ├── streaming/INC-4211  ← learned    {"symptom": "checkpoint timeout ...", "resolution": "parallelism 4->8 ..."}
    └── analytics/INC-0870               {"symptom": "Trino scans slow ... small files", "resolution": "..."}
```

The main operations:

| Call | Does |
| --- | --- |
| `store.put(ns, key, value)` | Create, or **overwrite** if the key already exists |
| `store.get(ns, key)` | One `Item` (`.namespace`, `.key`, `.value`, `.created_at`, `.updated_at`) or `None` |
| `store.search(ns_prefix, query=..., filter=..., limit=...)` | Many `SearchItem`s (an `Item` plus a `.score`) |
| `store.list_namespaces()` | All "folders" |
| `store.delete(ns, key)` | Remove one item |

**The namespace in `search` is a prefix.** `search(("episodes", "streaming"))` sees only the streaming episodes. But `search(("episodes",))` sees **every team's** episodes (verified: it returned INC-0870 from analytics alongside the streaming ones). Team isolation works only because the code **always** puts the team in the namespace. The store doesn't enforce it.

---

## 4. Semantic search: finding by meaning, not exact words

INC-4300's wording ("checkpoints failing") differs from INC-4211's ("checkpoint timeout"). Exact-match lookup won't connect them. **Embeddings** will.

### 4.1 Embeddings in one paragraph

An embedding model turns text into a list of numbers (a vector) so that **similar meanings give similar vectors**. To compare two texts, compare their vectors with **cosine similarity**: 1.0 = same direction (same meaning), 0.0 = nothing in common.

### 4.2 The chapter's fake embedding model (`sim/keyword_embeddings.py`)

A real model (e.g. `nomic-embed-text` via `--ollama`) needs Ollama. So the chapter ships a tiny deterministic stand-in: **one dimension per on-call concept**, set to 1 if any synonym appears.

```text
dims:  checkpoint  failure  backpressure  join  memory  lag  small_files  scan
"checkpoint timeout with high backpressure on join"   → [1, 1, 1, 1, 0, 0, 0, 0]
"checkpoints failing, join operator backpressured"    → [1, 1, 1, 1, 0, 0, 0, 0]   (synonyms!)
"TaskManager OOM, heap at 98%"                        → [0, 0, 0, 0, 1, 0, 0, 0]
```

- INC-4300 vs INC-4211: identical vectors → cosine **1.0**
- INC-4300 vs INC-0990: no shared concept → cosine **0.0**

That's exactly the demo's `raw ranking: [('INC-4211', 1.0), ('INC-0990', 0.0)]`. A real model gives in-between numbers (e.g. 0.83, 0.41), but works the same way.

### 4.3 Turning on semantic search: the `index` config

```python
store = InMemoryStore(index={
    "embed": KeywordEmbeddings(),   # the model that turns text into vectors
    "dims": DIMS,                   # vector length (8 here; 768 for nomic-embed-text)
    "fields": ["symptom"],          # WHICH field of each value to embed
})
```

- On `put`, the store embeds `value["symptom"]` and keeps the vector next to the item. Only the symptom is embedded, because that's what you'll search by. The resolution is just carried along.
- On `search(ns, query="...")`, it embeds the query, compares it to every item's vector in that namespace, and returns them **sorted by score**.
- `put(..., index=False)` stores an item **without** a vector. The seed does this for facts and runbooks: they have no `symptom` field and are only ever listed, never searched by meaning. Searching them returns `score=None`.
- **No `index` at all** → `query` is silently ignored. Every result has `score=None`, unranked.

### 4.4 Why `min_score` exists

`search` **always returns up to `limit` results**, however weak. INC-0990 (an OOM fix) came back with score 0.0 for a checkpoint problem. Without a threshold, the agent would be told "here's a similar past fix: raise heap to 8g", which is wrong. So `recall` keeps only hits with `score >= min_score` (0.5).

---

## 5. Three kinds of long-term memory

The seed data (`sim/seed_memories.py`) holds one of each:

| Kind | Meaning | Namespace | Example | Used by the graph? |
| --- | --- | --- | --- | --- |
| **Semantic** | Facts about the world | `facts/streaming` | "orders-enricher is owned by streaming" | No, seeded only as an example |
| **Episodic** | Past events: what happened and what fixed it | `episodes/<team>` | INC-0990, INC-0870, INC-4211 | Yes: searched by meaning in `recall`, written by `learn` |
| **Procedural** | Rules: how we do things | `runbooks/<team>` | "Take a savepoint before restarting any Flink job." | Yes: listed in `recall` |

These are categories for **you** to organise data. The Store treats them all the same.

---

## 6. Runtime context: how nodes reach the store and the team

```python
@dataclass
class OnCallContext:
    team: str
    min_score: float = 0.5

def recall(state: IncidentState, runtime: Runtime[OnCallContext]) -> dict:
    team, store = runtime.context.team, runtime.store
```

- If a node declares a `runtime: Runtime[...]` parameter, LangGraph passes it in.
  - `runtime.store` is the store you gave `compile(store=...)`. If you didn't pass one, it's `None` and you get `AttributeError: 'NoneType' object has no attribute 'search'`.
  - `runtime.context` is the object you pass per call: `graph.invoke(input, config, context=OnCallContext("streaming"))`.
- `StateGraph(IncidentState, context_schema=OnCallContext)` declares that type.

**Context vs state** (from Ch 2):

| | State | Context |
| --- | --- | --- |
| Changes during the run? | Yes, nodes update it | No, fixed for the whole run |
| Checkpointed? | Yes | **No**. Verified: the word "streaming" appears nowhere in the saved checkpoint |
| Good for | Data being worked on | Who is calling, settings: team, user id, thresholds |

That's why `team` lives in context. It's *who's asking*, not *what we're working on*, and it picks the namespace.

---

## 7. The code, top to bottom (`runbook_memory.py`)

### The graph

```text
START → recall → learn → END
```

Super-steps per run (from `get_state_history`): `step 0` input → `step 1` `recall` → `step 2` `learn` → done.

### `recall`: read from long-term memory

```python
hits = store.search(("episodes", team), query=state["symptom"], limit=3)    # semantic search
recalled = [f"{h.key} ({h.score:.2f}): {h.value['resolution']}"
            for h in hits if h.score is not None and h.score >= runtime.context.min_score]
rules = [h.value["text"] for h in store.search(("runbooks", team))]          # list, no query
return {"recalled": recalled, "rules": rules}
```

1. Search **this team's episodes** by meaning of the current symptom, top 3.
2. Keep only strong matches, formatted as `"INC-4211 (1.00): <fix>"`.
3. List **all of this team's runbook rules**, no query.
4. Write both into **state** (short-term), so later nodes (an LLM in a real agent) can use them.

### `learn`: write to long-term memory

```python
if state.get("resolution"):
    runtime.store.put(("episodes", team), state["incident_id"],
                      {"symptom": state["symptom"], "resolution": state["resolution"]})
return {}
```

- Only **closed** incidents (with a `resolution`) are worth remembering.
- The key is the incident id, so the same id **overwrites** the earlier entry.
- It returns `{}` because it changes no state. Its effect is a **side effect** on the store.
- In INC-4211's own run, `recall` runs **before** `learn`, so it doesn't recall itself (`recalled: []`).

### `make_store`: build and seed

Creates the `InMemoryStore` with the `index` config, chooses keyword or Ollama embeddings, then `put`s the seed. Items flagged `False` in `SEED` get `index=False`, meaning no vector.

### `main`: the 5-step demo, with actual output

```text
1) namespaces in the store
   ('episodes', 'analytics') ['INC-0870']
   ('episodes', 'streaming') ['INC-0990']
   ('facts', 'streaming') ['orders-enricher']
   ('runbooks', 'streaming') ['before-restart']
```
The seeded store, before any incidents.

```text
2) INC-4211 is closed with a resolution -> learn() stores the episode
   stored: {'symptom': 'checkpoint timeout with high backpressure on join', 'resolution': 'raised enrich-join parallelism 4->8, ...'}
```
Thread INC-4211 runs. `learn` writes the episode to `episodes/streaming/INC-4211`.

```text
3) INC-4300 (new thread, new wording) -> recall() finds INC-4211's fix
   raw ranking: [('INC-4211', 1.0), ('INC-0990', 0.0)] (min_score=0.5)
   recalled: ['INC-4211 (1.00): raised enrich-join parallelism 4->8, enabled unaligned checkpoints']
   rules:    ['Take a savepoint before restarting any Flink job.']
```
**The key moment.** A different thread, with different words, finds the fix. INC-0990 is dropped by `min_score`. It also gets the runbook rule.

```text
4) same symptom, team 'analytics' -> streaming memories are invisible
   recalled: []  rules: []
```
Same input, but `context.team = "analytics"` → it searches `episodes/analytics` → nothing relevant.

```text
5) thread INC-4300 state keys: [...no 'resolution'...]
```
Short-term memory stayed separate. Only the store crossed threads.

---

## 8. Gotchas (verified)

| Mistake | Result |
| --- | --- |
| No `index` on the store | `query` ignored, all `score=None`, `recall` keeps nothing |
| `min_score=0.0` | Irrelevant fixes (INC-0990) get recalled |
| `compile()` without `store=` | `runtime.store` is `None` → `AttributeError` |
| Searching a short prefix like `("episodes",)` | Sees **all teams**. Isolation comes from your namespace choice |
| Two incidents with the same `incident_id` | The second `put` overwrites the first |
| `InMemoryStore` | Lost when the process exits. Use a persistent store (e.g. Postgres) for real use |

---

## 9. The whole chapter in five lines

1. **Checkpointer** = memory of *this* conversation/thread. Automatic, private.
2. **Store** = memory shared across *all* threads. Explicit `put` / `search`, organised as `namespace / key → JSON`.
3. **Embeddings + `index`** let `search(query=...)` find past items by meaning. **Filter by score**, because search always returns something.
4. **Runtime context** (`team`) chooses the namespace, and the namespace is the access boundary.
5. The pattern: **recall** (search the store → put hits into state) → *work* → **learn** (put the outcome back into the store).
