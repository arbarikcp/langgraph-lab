"""Ch 16: an incident-investigation harness as an explicit StateGraph.

prefetch (cached) → act ⇄ tools → verify → (act with feedback | END)
Budgets (steps, tool calls, cost) live in state; when one runs out, act forces a final answer.
Runs anywhere with a scripted model and a rule-based judge; --ollama uses the LLM for both.
Run:  python -m chapters.ch16_custom_harness.rca_harness [--ollama]
"""
import operator
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.cache.memory import InMemoryCache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import CachePolicy, Command, RetryPolicy
from pydantic import BaseModel

from chapters.ch16_custom_harness.rca_tools import TOOLS
from chapters.ch16_custom_harness.sim import platform
from chapters.ch16_custom_harness.sim.scripted_model import ScriptedChatModel, call

PRICE_PER_TOKEN = {"input": 5 / 1_000_000, "output": 15 / 1_000_000}      # pretend prices, USD
SYSTEM = ("You are an on-call agent. Gather evidence with tools, then state the root cause and the next "
          "action. Every claim must cite a tool result.")
NUMBER = re.compile(r"\d[\d,.]*%?")
ACTION = re.compile(r"\b(scale|raise|increase|expire|compact|rewrite|restart)\b", re.I)


class RCAState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    table: str
    table_context: str
    steps: int
    tool_calls: int
    cost_usd: Annotated[float, operator.add]      # each act adds its own cost
    attempts: int                                  # verification rounds used
    stop_reason: str                               # which budget forced the answer, if any
    verdict: str


class Verdict(BaseModel):
    ok: bool
    feedback: str


@dataclass(frozen=True)
class Limits:
    max_steps: int = 10
    max_tool_calls: int = 8
    max_cost_usd: float = 0.50
    max_attempts: int = 2


@dataclass
class RCAContext:
    model: object                                  # chat model; bound to TOOLS per call
    judge: Callable[[str, str], Verdict]           # (evidence, answer) -> Verdict
    limits: Limits = Limits()


def rule_judge(evidence: str, answer: str) -> Verdict:
    """Deterministic verifier: cite at least one figure from the evidence and name a concrete action."""
    missing = []
    if not any(n in evidence for n in NUMBER.findall(answer)):
        missing.append("cite a figure from the tool results")
    if not ACTION.search(answer):
        missing.append("give a concrete next action")
    return Verdict(ok=not missing, feedback="; ".join(missing))


def llm_judge(model) -> Callable[[str, str], Verdict]:
    judge = model.with_structured_output(Verdict)
    return lambda evidence, answer: judge.invoke(
        f"EVIDENCE:\n{evidence}\n\nANSWER:\n{answer}\n\n"
        "Pass only if every claim is supported by the evidence and there is a concrete next action.")


def cost_of(msg: AIMessage) -> float:
    usage = msg.usage_metadata or {}
    return (usage.get("input_tokens", 0) * PRICE_PER_TOKEN["input"]
            + usage.get("output_tokens", 0) * PRICE_PER_TOKEN["output"])


def over_budget(state: RCAState, limits: Limits) -> str:
    if state.get("steps", 0) >= limits.max_steps:
        return "steps"
    if state.get("tool_calls", 0) >= limits.max_tool_calls:
        return "tool calls"
    if state.get("cost_usd", 0.0) >= limits.max_cost_usd:
        return "cost"
    return ""


# --- nodes ------------------------------------------------------------------------------------

def prefetch(state: RCAState) -> dict:
    """Expensive and deterministic: a metadata scan. Cached across threads, keyed on the table only."""
    return {"table_context": platform.CATALOG.describe(state["table"])}


def act(state: RCAState, runtime: Runtime[RCAContext]) -> Command[Literal["tools", "verify"]]:
    ctx = runtime.context
    reason = over_budget(state, ctx.limits)
    prompt = [SystemMessage(f"{SYSTEM}\nTable: {state['table_context']}"), *state["messages"]]
    if reason:                                     # forced answer: no tools offered
        prompt.append(HumanMessage(f"Budget exhausted ({reason}). Answer now with the evidence you have."))
    msg = (ctx.model if reason else ctx.model.bind_tools(TOOLS)).invoke(prompt)
    if reason and msg.tool_calls:                  # the harness, not the model, guarantees the stop
        msg = msg.model_copy(update={"tool_calls": []})
    return Command(
        update={"messages": [msg], "steps": state.get("steps", 0) + 1,
                "tool_calls": state.get("tool_calls", 0) + len(msg.tool_calls),
                "cost_usd": cost_of(msg), "stop_reason": reason},
        goto="tools" if msg.tool_calls else "verify")


def verify(state: RCAState, runtime: Runtime[RCAContext]) -> Command[Literal["act", "__end__"]]:
    answer = state["messages"][-1].content
    evidence = "\n".join(m.content for m in state["messages"] if m.type == "tool")
    verdict = runtime.context.judge(evidence, answer)
    attempts = state.get("attempts", 0) + 1
    if verdict.ok or attempts >= runtime.context.limits.max_attempts:
        return Command(update={"attempts": attempts, "verdict": "pass" if verdict.ok else "fail-unverified"},
                       goto=END)
    return Command(update={"attempts": attempts,
                           "messages": [HumanMessage(f"Reviewer feedback: {verdict.feedback}. Revise.")]},
                   goto="act")


def build_harness():
    builder = StateGraph(RCAState, context_schema=RCAContext)
    builder.add_node("prefetch", prefetch, cache_policy=CachePolicy(ttl=300, key_func=lambda s: s.get("table", "")))   # drawing calls it on {}
    builder.add_node("act", act, retry_policy=RetryPolicy(max_attempts=3, initial_interval=0.05, jitter=False,
                                                          retry_on=ConnectionError))     # flaky model API
    builder.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))                  # contain tool errors
    builder.add_node("verify", verify)
    builder.add_edge(START, "prefetch")
    builder.add_edge("prefetch", "act")
    builder.add_edge("tools", "act")
    return builder.compile(checkpointer=InMemorySaver(), cache=InMemoryCache())


# --- demo -------------------------------------------------------------------------------------

QUESTION = "orders-enricher lags and sales dashboards are stale. Root cause?"


def tools_turn(*calls: dict) -> AIMessage:
    return AIMessage("", tool_calls=list(calls))


def flaky_run_script() -> list:
    """Exercise 1 + verifier loop: model API fails once, Trino fails on every 2nd query, first answer is vague."""
    return [ConnectionError("model API 503"),
            tools_turn(call("flink_job_status", job="orders-enricher"),
                       call("taskmanager_metrics", job="orders-enricher")),
            tools_turn(call("run_trino_query", sql='SELECT * FROM "sales.orders$snapshots"')),
            tools_turn(call("run_trino_query", sql='SELECT * FROM "sales.orders$files"')),       # 503
            tools_turn(call("run_trino_query", sql='SELECT * FROM "sales.orders$files"')),       # retry, ok
            AIMessage("orders-enricher is struggling."),                                         # rejected
            AIMessage("Root cause: backpressure HIGH on 'enrich-join' with heap=92% at parallelism=4, so "
                      "orders-enricher lags 2,400,000 records and sales.orders' newest snapshot is 47 min old. "
                      "Next: scale orders-enricher to parallelism 8.")]


def cost_run_script() -> list:
    """Exercise 2: the model keeps investigating; the cost budget forces the answer."""
    return [tools_turn(call("flink_job_status", job="orders-enricher"),
                       call("taskmanager_metrics", job="orders-enricher")),
            tools_turn(call("run_trino_query", sql='SELECT * FROM "sales.orders$snapshots"')),
            tools_turn(call("run_trino_query", sql='SELECT * FROM "sales.orders$files"')),
            AIMessage("Evidence so far: lag 2,400,000 records, backpressure HIGH on 'enrich-join', heap=92%. "
                      "Next: scale orders-enricher to parallelism 8. Let me also count the rows.",
                      tool_calls=[call("run_trino_query", sql="SELECT count(*) FROM sales.orders")])]  # call stripped


def investigate(graph, thread_id: str, ctx: RCAContext) -> dict:
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}       # outer safety net
    for update in graph.stream({"messages": [HumanMessage(QUESTION)], "table": "sales.orders"}, cfg, context=ctx, stream_mode="updates"):
        cached = update.pop("__metadata__", {}).get("cached", False)      # cache hit marker
        for node, delta in update.items():
            msgs = delta.get("messages", [])
            if node == "act":
                m = msgs[0]
                what = f"calls {[c['name'] for c in m.tool_calls]}" if m.tool_calls else f"answer: {m.content[:70]}…"
                forced = f"  ⚠ forced ({delta['stop_reason']})" if delta["stop_reason"] else ""
                print(f"  act #{delta['steps']}: {what}  (+${delta['cost_usd']:.3f}){forced}")
            elif node == "tools":
                for m in msgs:
                    print(f"    tool {m.name}: {m.status}" + (f" — {m.content.splitlines()[0][:60]}" if m.status == "error" else ""))
            elif node == "verify":
                fb = msgs[0].content if msgs else ""
                print(f"  verify: {delta.get('verdict') or 'rejected'}  {fb}")
            elif node == "prefetch":
                print(f"  prefetch{' (cache hit)' if cached else ''}: {delta['table_context']}")
    return graph.get_state(cfg).values


def main() -> None:
    use_ollama = "--ollama" in sys.argv
    graph = build_harness()
    if use_ollama:
        from common.llm import llm
    platform.CATALOG = platform.FakeCatalog()

    print("1) flaky model API + flaky Trino + verifier feedback")
    platform.TRINO = platform.FakeTrino(fail_every=2)
    model = llm if use_ollama else ScriptedChatModel(replies=flaky_run_script())
    ctx = RCAContext(model, llm_judge(llm) if use_ollama else rule_judge)
    out = investigate(graph, "RCA-1", ctx)
    print(f"  → verdict={out['verdict']} steps={out['steps']} tool_calls={out['tool_calls']} "
          f"cost=${out['cost_usd']:.3f}")
    if not use_ollama:
        print(f"  model calls: {model.calls} for {out['steps']} act steps (RetryPolicy re-ran act after the 503)")

    print("\n2) cost budget $0.03 (pretend prices): the answer is forced")
    platform.TRINO = platform.FakeTrino()
    model = llm if use_ollama else ScriptedChatModel(replies=cost_run_script())
    ctx = RCAContext(model, llm_judge(llm) if use_ollama else rule_judge, Limits(max_cost_usd=0.03, max_attempts=1))
    out = investigate(graph, "RCA-2", ctx)
    print(f"  → verdict={out['verdict']} stop_reason={out['stop_reason']!r} cost=${out['cost_usd']:.3f}")
    forced = out["messages"][-1]
    print(f"  forced answer (tool calls stripped: {forced.tool_calls}): {forced.content}")
    print(f"\ncatalog metadata scans for 2 threads: {platform.CATALOG.loads} (prefetch is cached)")


if __name__ == "__main__":
    main()
