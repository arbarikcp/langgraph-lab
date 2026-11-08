"""Ch 13: context engineering + structured output — per-team prompt and tools, model escalation, IncidentReport.

Runs anywhere with scripted models (sim/scripted_model.py); add --ollama for small_llm → llm.
Run:  python -m chapters.ch13_context_output.triage_report [--ollama]
"""
import sys
from typing import Literal

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, dynamic_prompt
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

from chapters.ch13_context_output.sim.scripted_model import ScriptedChatModel, call
from chapters.ch13_context_output.triage_tools import TEAM_TOOLS, TOOLS, TriageContext

ESCALATE_AFTER = 4          # messages in the conversation before we switch to the bigger model


class IncidentReport(BaseModel):
    """Final triage report for an incident."""
    severity: Literal["P1", "P2", "P3"]
    root_cause: str = Field(description="One sentence, backed by tool evidence")
    evidence: list[str] = Field(description="Tool outputs that support the root cause")
    actions: list[str]
    needs_human: bool


@dynamic_prompt
def role_prompt(request: ModelRequest) -> str:
    """The system prompt is built per call from runtime context. It is never stored in state."""
    team = request.runtime.context.team
    return (f"You are the on-call assistant for team '{team}'. Investigate with the tools, "
            "cite tool evidence for every claim, never guess metrics. "
            "Finish by returning an IncidentReport.")


class TeamToolAccess(AgentMiddleware):
    """Least privilege per caller. Hiding a tool (model side) is context; refusing it (tool side) is security."""

    def wrap_model_call(self, request: ModelRequest, handler):
        allowed = TEAM_TOOLS[request.runtime.context.team]
        return handler(request.override(tools=[t for t in request.tools if t.name in allowed]))

    def wrap_tool_call(self, request, handler):
        tool_call = request.tool_call
        if tool_call["name"] not in TEAM_TOOLS[request.runtime.context.team]:
            return ToolMessage(f"DENIED: team may not use {tool_call['name']}.", tool_call_id=tool_call["id"],
                               name=tool_call["name"], status="error")
        return handler(request)


class ModelEscalation(AgentMiddleware):
    """Start on the small model; switch to the big one once the investigation gets long."""

    def __init__(self, big_model, after: int = ESCALATE_AFTER):
        super().__init__()
        self.big_model, self.after = big_model, after

    def wrap_model_call(self, request: ModelRequest, handler):
        if len(request.messages) > self.after:
            request = request.override(model=self.big_model)
        return handler(request)


def build_agent(small_model, big_model):
    return create_agent(
        model=small_model,
        tools=TOOLS,
        context_schema=TriageContext,
        middleware=[role_prompt, TeamToolAccess(), ModelEscalation(big_model)],
        response_format=ToolStrategy(IncidentReport),   # what "auto" picks for Ollama anyway
    )


# --- demo -------------------------------------------------------------------------------------

QUESTION = "Trino scans on sales.orders are 5x slower since Monday. Triage it."

GOOD_REPORT = dict(
    severity="P2",
    root_cause="orders-sink commits every 30s, so sales.orders piled up 1,240 snapshots and 38% small "
               "files with no snapshot expiry for 41 days; scan planning went from 3s to 18s.",
    evidence=["table_health: 1,240 snapshots, expire_snapshots 41 days ago, 38% files < 16 MB",
              "table_health: scan planning 18s (was 3s)"],
    actions=["CALL system.expire_snapshots('sales.orders')", "CALL system.rewrite_data_files('sales.orders')",
             "Raise the orders-sink commit interval"],
    needs_human=True,
)


def scripts(team: str) -> tuple[list[AIMessage], list[AIMessage]]:
    """(small model replies, big model replies) for each scenario."""
    if team == "analytics":
        small = [AIMessage("", tool_calls=[call("my_tables")]),
                 AIMessage("", tool_calls=[call("table_health", table="sales.orders")])]
        big = [AIMessage("", tool_calls=[call("IncidentReport", **{**GOOD_REPORT, "severity": "P0"})]),  # invalid
               AIMessage("", tool_calls=[call("IncidentReport", **GOOD_REPORT)])]
    else:
        small = [AIMessage("", tool_calls=[call("table_health", table="sales.orders")]),
                 AIMessage("", tool_calls=[call("flink_job_status", job="orders-sink")])]
        big = [AIMessage("", tool_calls=[call("IncidentReport", **GOOD_REPORT)])]
    return small, big


def make_models(team: str, use_ollama: bool):
    if use_ollama:
        from common.llm import llm, small_llm
        return small_llm, llm
    small, big = scripts(team)
    return ScriptedChatModel(label="small", replies=small), ScriptedChatModel(label="big", replies=big)


def triage(ctx: TriageContext, use_ollama: bool) -> dict:
    small, big = make_models(ctx.team, use_ollama)
    agent = build_agent(small, big)
    result = agent.invoke({"messages": [{"role": "user", "content": QUESTION}]},
                          {"recursion_limit": 25}, context=ctx)
    if not use_ollama:
        print("  what the model saw, call by call:")
        for i, seen in enumerate(sorted(small.seen + big.seen, key=lambda s: s["messages"]), 1):
            print(f"    {i}. model={seen['model']:<5} messages={seen['messages']} (incl. system)  tools={seen['tools']}")
        print(f"  system prompt: {small.seen[0]['system'][:70]}…")
    stored = result["messages"]
    print(f"  stored in state: {len(stored)} messages, types={[m.type for m in stored]}")
    errors = [m.content for m in stored if m.type == "tool" and "error" in m.content.lower()]
    if errors:
        print(f"  structured-output retry: {errors[0][:110]}…")
    return result


def main() -> None:
    use_ollama = "--ollama" in sys.argv
    for ctx in (TriageContext("u7", "analytics"), TriageContext("u42", "streaming")):
        print(f"\n== {ctx.user_id} (team {ctx.team}) ==")
        report: IncidentReport = triage(ctx, use_ollama)["structured_response"]
        print(f"  structured_response: {type(report).__name__}(severity={report.severity!r}, "
              f"needs_human={report.needs_human})")
        print(f"    root_cause: {report.root_cause}")
        print(f"    actions: {report.actions}")


if __name__ == "__main__":
    main()
