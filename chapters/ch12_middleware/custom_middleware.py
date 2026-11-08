"""Ch 12: hand-written middleware — a hook tracer, a SQL policy, an audit log and a model-call budget.

Runs anywhere with a scripted model (sim/scripted_model.py); add --ollama to let the real LLM drive.
Run:  python -m chapters.ch12_middleware.custom_middleware [--ollama]
"""
import re
import sys
import time
from typing import NotRequired

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware, hook_config, wrap_tool_call
from langchain_core.messages import AIMessage, ToolMessage

from chapters.ch12_middleware.ops_tools import TOOLS
from chapters.ch12_middleware.sim import lakehouse_ops
from chapters.ch12_middleware.sim.scripted_model import ScriptedChatModel, call

WRITE_SQL = re.compile(r"\b(DROP|DELETE|TRUNCATE|ALTER|INSERT|UPDATE)\b", re.I)
AUDIT: list[dict] = []          # what audit_log recorded; a real harness ships this to a log pipeline


class HookTracer(AgentMiddleware):
    """Prints and records every hook, to show the order they fire in."""

    def __init__(self, label: str, trace: list[str]):
        super().__init__()
        self.label, self.trace = label, trace

    @property
    def name(self) -> str:
        return self.label

    def _log(self, hook: str) -> None:
        self.trace.append(f"{self.label}.{hook}")
        print(f"  {self.label}.{hook}")

    def before_agent(self, state, runtime):
        self._log("before_agent")

    def before_model(self, state, runtime):
        self._log("before_model")

    def wrap_model_call(self, request, handler):
        self._log("wrap_model_call >")
        response = handler(request)
        self._log("wrap_model_call <")
        return response

    def after_model(self, state, runtime):
        self._log("after_model")

    def wrap_tool_call(self, request, handler):
        self._log("wrap_tool_call >")
        result = handler(request)
        self._log("wrap_tool_call <")
        return result

    def after_agent(self, state, runtime):
        self._log("after_agent")


@wrap_tool_call
def sql_policy(request, handler):
    """Guardrail: write SQL never reaches Trino. Not calling handler() = the tool does not run."""
    tool_call = request.tool_call
    if tool_call["name"] == "run_trino_query" and WRITE_SQL.search(tool_call["args"].get("sql", "")):
        return ToolMessage("BLOCKED by policy: read-only SQL only.", tool_call_id=tool_call["id"],
                           name=tool_call["name"], status="error")
    return handler(request)


@wrap_tool_call
def audit_log(request, handler):
    """Observability: one record per tool call, including the ones the policy blocked."""
    start = time.perf_counter()
    result = handler(request)
    AUDIT.append({"tool": request.tool_call["name"], "args": request.tool_call["args"],
                  "status": getattr(result, "status", "success"),
                  "ms": round((time.perf_counter() - start) * 1000, 2)})
    return result


class BudgetState(AgentState):
    model_calls: NotRequired[int]


class ModelBudget(AgentMiddleware):
    """Loop budget: stop the run before model call number limit + 1. Adds `model_calls` to state."""

    state_schema = BudgetState

    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        used = state.get("model_calls", 0)
        if used >= self.limit:
            return {"jump_to": "end",
                    "messages": [AIMessage(f"Stopped: model-call budget of {self.limit} used up.")]}
        return {"model_calls": used + 1}


def build_agent(model, middleware: list):
    return create_agent(model=model, tools=TOOLS, middleware=middleware,
                        system_prompt="You are a lakehouse on-call assistant. Use the tools; be brief.")


# --- demo -------------------------------------------------------------------------------------

SCRIPTS = {
    "trace": [AIMessage("", tool_calls=[call("run_trino_query", sql="SELECT * FROM sales.orders")]),
              AIMessage("sales.orders: 1,240 snapshots, 412 GB.")],
    "policy": [AIMessage("", tool_calls=[call("run_trino_query", sql="DROP TABLE sales.orders_tmp")]),
               AIMessage("", tool_calls=[call("run_trino_query", sql="SELECT * FROM sales.orders")]),
               AIMessage("I can't drop tables (read-only policy). sales.orders is 412 GB.")],
    "budget": [AIMessage("", tool_calls=[call("run_trino_query", sql="SELECT * FROM sales.orders")])],  # loops forever
}
PROMPTS = {
    "trace": "How big is sales.orders?",
    "policy": "Drop the scratch table sales.orders_tmp, then tell me how big sales.orders is.",
    "budget": "Keep querying sales.orders until the snapshot count drops below 100.",
}


def run(scenario: str, middleware: list, use_ollama: bool) -> dict:
    lakehouse_ops.OPS = lakehouse_ops.LakehouseOps()       # a fresh cluster per scenario
    if use_ollama:
        from common.llm import llm
        model = llm
    else:
        model = ScriptedChatModel(replies=SCRIPTS[scenario])
    agent = build_agent(model, middleware)
    return agent.invoke({"messages": [{"role": "user", "content": PROMPTS[scenario]}]}, {"recursion_limit": 30})


def main() -> None:
    use_ollama = "--ollama" in sys.argv

    print("1) hook order with middleware=[outer, inner] (one tool round, then the answer):")
    trace: list[str] = []
    run("trace", [HookTracer("outer", trace), HookTracer("inner", trace)], use_ollama)

    print("\n2) SQL policy + audit log, middleware=[audit_log, sql_policy]:")
    out = run("policy", [audit_log, sql_policy], use_ollama)
    for msg in out["messages"]:
        if msg.type == "tool":
            print(f"  [tool:{msg.status}] {msg.content}")
    print(f"  answer: {out['messages'][-1].content}")
    print(f"  queries Trino really ran: {lakehouse_ops.OPS.queries}")
    print("  audit:", *AUDIT, sep="\n    ")

    print("\n3) ModelBudget(limit=3) on a model that never stops calling tools:")
    out = run("budget", [ModelBudget(limit=3)], use_ollama)
    print(f"  model_calls={out.get('model_calls')}  last message: {out['messages'][-1].content}")


if __name__ == "__main__":
    main()
