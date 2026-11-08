"""Ch 16, exercise 3: the same harness rebuilt with create_agent + middleware, run on the same script.

Budgets → ForcedAnswerBudget (+ ModelCallLimitMiddleware as backstop), tool errors → ContainToolErrors,
model API retry → ModelRetryMiddleware, verifier loop → AnswerVerifier.after_agent with jump_to "model".
Run:  python -m chapters.ch16_custom_harness.agent_rebuild
"""
from typing import NotRequired

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, ModelRetryMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from chapters.ch16_custom_harness.rca_harness import (
    QUESTION, SYSTEM, Limits, build_harness, cost_of, flaky_run_script, investigate, rule_judge, RCAContext,
)
from chapters.ch16_custom_harness.rca_tools import TOOLS
from chapters.ch16_custom_harness.sim import platform
from chapters.ch16_custom_harness.sim.scripted_model import ScriptedChatModel


class VerifiedState(AgentState):
    attempts: NotRequired[int]
    verdict: NotRequired[str]


class ForcedAnswerBudget(AgentMiddleware):
    """Budgets, derived from the messages in state. Over budget: no tools offered, any tool call stripped."""

    def __init__(self, limits: Limits):
        super().__init__()
        self.limits = limits

    def wrap_model_call(self, request, handler):
        ai = [m for m in request.state["messages"] if m.type == "ai"]
        over = (len(ai) >= self.limits.max_steps
                or sum(len(m.tool_calls) for m in ai) >= self.limits.max_tool_calls
                or sum(cost_of(m) for m in ai) >= self.limits.max_cost_usd)
        if not over:
            return handler(request)
        nudge = HumanMessage("Budget exhausted. Answer now with the evidence you have.")
        response = handler(request.override(tools=[], messages=[*request.messages, nudge]))
        msg = response.result[0]
        return msg.model_copy(update={"tool_calls": []}) if msg.tool_calls else response


class ContainToolErrors(AgentMiddleware):
    """Tool exceptions become error ToolMessages, like ToolNode(handle_tool_errors=True)."""

    def wrap_tool_call(self, request, handler):
        try:
            return handler(request)
        except Exception as err:  # noqa: BLE001 — containment is the point
            return ToolMessage(f"Error: {err!r}. Try again or use other evidence.", status="error",
                               tool_call_id=request.tool_call["id"], name=request.tool_call["name"])


class AnswerVerifier(AgentMiddleware):
    """The verifier loop: after the agent finishes, judge the answer; if weak, add feedback and loop back."""

    state_schema = VerifiedState

    def __init__(self, judge, max_attempts: int):
        super().__init__()
        self.judge, self.max_attempts = judge, max_attempts

    @hook_config(can_jump_to=["model"])
    def after_agent(self, state, runtime):
        evidence = "\n".join(m.content for m in state["messages"] if m.type == "tool")
        verdict = self.judge(evidence, state["messages"][-1].content)
        attempts = state.get("attempts", 0) + 1
        if verdict.ok or attempts >= self.max_attempts:
            return {"attempts": attempts, "verdict": "pass" if verdict.ok else "fail-unverified"}
        return {"attempts": attempts, "jump_to": "model",
                "messages": [HumanMessage(f"Reviewer feedback: {verdict.feedback}. Revise.")]}


def build_agent(model, judge=rule_judge, limits: Limits = Limits()):
    return create_agent(model, tools=TOOLS, system_prompt=SYSTEM, middleware=[
        ModelCallLimitMiddleware(run_limit=limits.max_steps + limits.max_attempts, exit_behavior="end"),
        ModelRetryMiddleware(max_retries=2, initial_delay=0.05, jitter=False),
        ForcedAnswerBudget(limits),
        ContainToolErrors(),
        AnswerVerifier(judge, limits.max_attempts),
    ])


def main() -> None:
    platform.CATALOG = platform.FakeCatalog()

    print("explicit graph  (rca_harness):")
    platform.TRINO = platform.FakeTrino(fail_every=2)
    graph_model = ScriptedChatModel(replies=flaky_run_script())
    graph_out = investigate(build_harness(), "CMP-1", RCAContext(graph_model, rule_judge))

    print("\ncreate_agent + middleware  (agent_rebuild):")
    platform.TRINO = platform.FakeTrino(fail_every=2)
    agent_model = ScriptedChatModel(replies=flaky_run_script())
    agent = build_agent(agent_model)
    agent_out = agent.invoke({"messages": [HumanMessage(QUESTION)]}, {"recursion_limit": 50})
    for m in agent_out["messages"][1:]:
        text = f"calls {[c['name'] for c in m.tool_calls]}" if getattr(m, "tool_calls", None) else m.content[:70]
        print(f"  {m.type:<5} {text}")

    print("\n                 explicit graph            create_agent")
    print(f"  nodes          {[n for n in build_harness().get_graph().nodes if not n.startswith('__')]}")
    print(f"                 {[n for n in agent.get_graph().nodes if not n.startswith('__')]}")
    print(f"  verdict        {graph_out['verdict']:<25} {agent_out['verdict']}")
    print(f"  verify rounds  {graph_out['attempts']:<25} {agent_out['attempts']}")
    print(f"  model calls    {graph_model.calls:<25} {agent_model.calls}")
    print(f"  final answer   same: {graph_out['messages'][-1].content == agent_out['messages'][-1].content}")


if __name__ == "__main__":
    main()
