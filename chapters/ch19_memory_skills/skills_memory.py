"""Ch 19: AGENTS.md (always loaded), skills (progressive disclosure) and /memories/ (Store, cross-thread).

The workspace is a fresh copy of oncall_ws/ on disk; /memories/ is routed to a Store.
Runs anywhere with scripted models; --ollama for the real LLM.
Run:  python -m chapters.ch19_memory_skills.skills_memory [--ollama]
"""
import sys

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend
from langchain.tools import tool
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from chapters.ch19_memory_skills.sim import platform
from chapters.ch19_memory_skills.sim.scripted_model import ScriptedChatModel, call
from chapters.ch19_memory_skills.sim.workspace import fresh_workspace

SYSTEM_PROMPT = "You are the streaming on-call engineer. Follow the team conventions and use the runbooks."
MEMORY_FILES = ["/AGENTS.md", "/memories/lessons.md"]       # loaded into the prompt once per thread
SKILL_SOURCES = ["/skills/"]                                 # only name + description loaded up front


@tool
def taskmanager_metrics(job: str) -> str:
    """Flink TaskManager metrics: backpressure, hot operator, heap, parallelism."""
    return platform.FLINK_METRICS.get(job, f"unknown job '{job}'")


def build_oncall_agent(model, workspace: str, store):
    return create_deep_agent(
        model=model,
        tools=[taskmanager_metrics],
        system_prompt=SYSTEM_PROMPT,
        memory=MEMORY_FILES,
        skills=SKILL_SOURCES,
        backend=CompositeBackend(
            default=FilesystemBackend(root_dir=workspace, virtual_mode=True),
            routes={"/memories/": StoreBackend(namespace=lambda runtime: ("oncall", "memories"), store=store)}),
        store=store,
        checkpointer=InMemorySaver(),
    )


LESSON = "- orders-enricher backpressures on enrich-join at parallelism 4 under peak load; raise to 6 (x1.5)."


def thread_1_script() -> list[AIMessage]:
    turn = lambda name, **args: AIMessage("", tool_calls=[call(name, **args)])  # noqa: E731
    return [turn("taskmanager_metrics", job="orders-enricher"),
            turn("read_file", file_path="/skills/flink-backpressure/SKILL.md"),      # loaded on demand
            turn("write_file", file_path="/memories/lessons.md", content=LESSON),
            AIMessage("Hot join + heap 92%: per the runbook, check state TTL, then raise enrich-join "
                      "parallelism 4 → 6 and enable unaligned checkpoints. Restart needs approval.")]


def thread_2_script() -> list[AIMessage]:
    return [AIMessage("Known pattern (from memory): enrich-join at parallelism 4 under peak load. "
                      "Raise to 6 after checking state TTL.")]


def run(agent, thread_id: str, question: str) -> dict:
    return agent.invoke({"messages": [{"role": "user", "content": question}]},
                        {"configurable": {"thread_id": thread_id}})


def check(prompt: str, needle: str) -> str:
    return "✔" if needle in prompt else "✗"


def main() -> None:
    use_ollama = "--ollama" in sys.argv
    if use_ollama:
        from common.llm import llm
    workspace, store = fresh_workspace(), InMemoryStore()
    question = "orders-enricher lag keeps growing and checkpoints fail. What should we do?"

    print(f"thread INC-1 (workspace {workspace}):")
    model_1 = llm if use_ollama else ScriptedChatModel(replies=thread_1_script())
    out = run(build_oncall_agent(model_1, workspace, store), "INC-1", question)
    if not use_ollama:
        prompt = model_1.system_prompts[0]
        print(f"  first system prompt: {len(prompt):,} chars")
        print(f"    {check(prompt, 'Never restart a Flink job')} AGENTS.md rule (always loaded)")
        print(f"    {check(prompt, 'Diagnose and fix Flink backpressure')} flink-backpressure description")
        print(f"    {check(prompt, 'Expire snapshots and compact')} iceberg-maintenance description")
        print(f"    {check(prompt, 'unaligned checkpoints')} flink-backpressure body (not loaded until read)")
    read = [m.tool_calls[0]["args"]["file_path"] for m in out["messages"]
            if m.type == "ai" and m.tool_calls and m.tool_calls[0]["name"] == "read_file"]
    print(f"  skills the agent read: {read}")
    print(f"  answer: {out['messages'][-1].content}")
    print(f"  Store now holds: {[item.key for item in store.search(('oncall', 'memories'))]}")

    print("\nthread INC-2 (new thread, same Store):")
    model_2 = llm if use_ollama else ScriptedChatModel(replies=thread_2_script())
    out = run(build_oncall_agent(model_2, workspace, store), "INC-2", question)
    if not use_ollama:
        print(f"  {check(model_2.system_prompts[0], 'raise to 6 (x1.5)')} lesson from INC-1 is in the system prompt")
    print(f"  answer: {out['messages'][-1].content}")


if __name__ == "__main__":
    main()
