"""Ch 14, pattern 1: subagents as tools. A supervisor delegates to two specialists and merges the answers.

Context isolation: a specialist sees only the question it was asked; the supervisor gets back only its reply.
Run:  python -m chapters.ch14_multi_agent.supervisor [--ollama]
"""
import sys

from langchain.agents import create_agent
from langchain.tools import tool

from chapters.ch14_multi_agent.sim import scripts
from chapters.ch14_multi_agent.sim.scripted_model import ScriptedChatModel
from chapters.ch14_multi_agent.specialists import INCIDENT, build_flink_agent, build_iceberg_agent

SUPERVISOR_PROMPT = ("Incident commander. Split the problem, delegate each part to the right specialist, "
                     "then merge their findings into one root cause and fix.")


def build_supervisor(supervisor_model, flink_model, iceberg_model, delegations: list[dict]):
    flink_agent = build_flink_agent(flink_model)
    iceberg_agent = build_iceberg_agent(iceberg_model)

    def delegate(agent, name: str, question: str) -> str:
        result = agent.invoke({"messages": [{"role": "user", "content": question}]})
        reply = result["messages"][-1].content
        delegations.append({"agent": name, "question": question,
                            "internal_messages": len(result["messages"]), "returned_chars": len(reply)})
        return reply                     # only the summary goes back, not the specialist's history

    @tool
    def ask_flink_specialist(question: str) -> str:
        """Delegate streaming questions: Flink jobs, lag, checkpoints, backpressure."""
        return delegate(flink_agent, "flink_agent", question)

    @tool
    def ask_iceberg_specialist(question: str) -> str:
        """Delegate lakehouse questions: Iceberg tables, snapshots, small files, Trino performance."""
        return delegate(iceberg_agent, "iceberg_agent", question)

    return create_agent(supervisor_model, tools=[ask_flink_specialist, ask_iceberg_specialist],
                        system_prompt=SUPERVISOR_PROMPT, name="supervisor")


def make_models(use_ollama: bool):
    if use_ollama:
        from common.llm import llm
        return llm, llm, llm
    return (ScriptedChatModel(label="supervisor", replies=scripts.supervisor_replies()),
            ScriptedChatModel(label="flink", replies=scripts.flink_replies()),
            ScriptedChatModel(label="iceberg", replies=scripts.iceberg_replies()))


def main() -> None:
    delegations: list[dict] = []
    supervisor = build_supervisor(*make_models("--ollama" in sys.argv), delegations)
    result = supervisor.invoke({"messages": [{"role": "user", "content": INCIDENT}]}, {"recursion_limit": 20})

    print(f"user → supervisor: {INCIDENT}\n")
    for d in delegations:
        print(f"[{d['agent']}] asked: {d['question']!r}")
        print(f"    ran {d['internal_messages']} messages inside, returned {d['returned_chars']} chars")
    print(f"\nsupervisor state: {len(result['messages'])} messages "
          f"{[m.type for m in result['messages']]}  (no specialist tool calls in here)")
    print(f"\nanswer:\n{result['messages'][-1].content}")


if __name__ == "__main__":
    main()
