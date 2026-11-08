"""Ch 14, pattern 2: handoffs. The triage agent transfers the conversation to the Flink specialist,
which then owns the thread: follow-ups go straight to it.

A transfer tool returns Command(goto=..., graph=Command.PARENT): "leave my subgraph, go to that node".
Run:  python -m chapters.ch14_multi_agent.handoff [--ollama]
"""
import sys

from langchain.agents import AgentState, create_agent
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command

from chapters.ch14_multi_agent.sim import scripts
from chapters.ch14_multi_agent.sim.scripted_model import ScriptedChatModel
from chapters.ch14_multi_agent.specialists import INCIDENT, build_flink_agent

TRIAGE_PROMPT = ("Triage agent. If the problem involves Flink jobs, lag or stale streaming data, "
                 "call transfer_to_flink. Do not diagnose it yourself.")


class TeamState(AgentState):
    active_agent: str                    # who owns the conversation now


@tool
def transfer_to_flink(reason: str, runtime: ToolRuntime) -> Command:
    """Hand the conversation to the Flink specialist."""
    transfer_call = runtime.state["messages"][-1]          # the AIMessage that called this tool
    done = ToolMessage(f"Transferred to flink_agent: {reason}", tool_call_id=runtime.tool_call_id)
    return Command(graph=Command.PARENT, goto="flink_agent",
                   update={"messages": [transfer_call, done], "active_agent": "flink_agent"})


def build_handoff_graph(triage_model, flink_model):
    triage_agent = create_agent(triage_model, tools=[transfer_to_flink], system_prompt=TRIAGE_PROMPT,
                                name="triage_agent")
    builder = StateGraph(TeamState)
    builder.add_node("triage_agent", triage_agent, destinations=("flink_agent",))
    builder.add_node("flink_agent", build_flink_agent(flink_model))
    builder.add_conditional_edges(START, lambda s: s.get("active_agent", "triage_agent"),
                                  ["triage_agent", "flink_agent"])
    return builder.compile(checkpointer=InMemorySaver())


def show(messages) -> None:
    for m in messages:
        calls = [c["name"] for c in getattr(m, "tool_calls", [])]
        text = f"calls {calls}" if calls else m.content.splitlines()[0] if m.content else ""
        print(f"  {m.type:<5} {m.name or '':<18} {text[:90]}")


def main() -> None:
    use_ollama = "--ollama" in sys.argv
    if use_ollama:
        from common.llm import llm
        triage_model = flink_model = llm
    else:
        triage_model = ScriptedChatModel(label="triage", replies=scripts.triage_replies())
        flink_model = ScriptedChatModel(label="flink", replies=scripts.flink_replies(follow_up=True))
    graph = build_handoff_graph(triage_model, flink_model)
    cfg = {"configurable": {"thread_id": "INC-4211"}}

    print("turn 1:", INCIDENT)
    out = graph.invoke({"messages": [{"role": "user", "content": INCIDENT}]}, cfg)
    show(out["messages"])
    print(f"  active_agent = {out['active_agent']}")

    n = len(out["messages"])
    print("\nturn 2: What should I change?")
    out = graph.invoke({"messages": [{"role": "user", "content": "What should I change?"}]}, cfg)
    show(out["messages"][n:])
    if not use_ollama:
        print(f"\nmodel calls: triage={triage_model.calls} (only turn 1), flink={flink_model.calls}")
        print(f"flink's first call saw {flink_model.seen[0]['messages']} messages, "
              f"incl. the user's own words: {flink_model.seen[0]['first_user'][:40]!r}…")


if __name__ == "__main__":
    main()
