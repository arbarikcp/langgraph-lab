"""Ch 18: where a deep agent's files live. One CompositeBackend, three routes, two threads.

  /            → StateBackend       (default; in the thread's checkpoint, gone on a new thread)
  /workspace/  → FilesystemBackend  (real disk under a temp dir, virtual_mode=True jails paths)
  /memories/   → StoreBackend       (LangGraph Store, shared by every thread)

Runs anywhere: scripted models issue the file tool calls.
Run:  python -m chapters.ch18_deepagents_api.backends_tour
"""
import os
import tempfile

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from chapters.ch18_deepagents_api.sim.scripted_model import ScriptedChatModel, call


def build_backend(root_dir: str, store) -> CompositeBackend:
    """Longest matching prefix wins; the prefix is stripped before the route sees the path."""
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/workspace/": FilesystemBackend(root_dir=root_dir, virtual_mode=True),
            "/memories/": StoreBackend(namespace=lambda runtime: ("oncall", "memories"), store=store),
        },
    )


def build_agent(model, root_dir: str, store):
    return create_deep_agent(model=model, tools=[], system_prompt="You keep notes in files.",
                             backend=build_backend(root_dir, store), store=store, checkpointer=InMemorySaver())


def file_op(name: str, **args) -> AIMessage:
    return AIMessage("", tool_calls=[call(name, **args)])


def thread_1_script() -> list[AIMessage]:
    return [file_op("write_file", file_path="/findings/orders-enricher.md", content="lag 2.4M; backpressure HIGH"),
            file_op("write_file", file_path="/workspace/report.md", content="# INC-4211 report\nScale to 8."),
            file_op("write_file", file_path="/memories/lessons.md", content="Expire sales.orders snapshots weekly."),
            file_op("read_file", file_path="/workspace/../../../etc/hosts"),                # escape attempt
            AIMessage("Notes written.")]


def thread_2_script() -> list[AIMessage]:
    return [file_op("ls", path="/findings"), file_op("ls", path="/workspace"), file_op("ls", path="/memories"),
            file_op("read_file", file_path="/memories/lessons.md"), AIMessage("Read my notes.")]


def tool_results(out: dict) -> list[str]:
    return [m.content.replace("\n", " ⏎ ") for m in out["messages"] if m.type == "tool"]


def main() -> None:
    root, store = tempfile.mkdtemp(prefix="oncall_ws_"), InMemoryStore()

    print("thread INC-1 writes one file per route, then tries to escape the disk jail:")
    out = build_agent(ScriptedChatModel(replies=thread_1_script()), root, store).invoke(
        {"messages": [{"role": "user", "content": "Take notes."}]}, {"configurable": {"thread_id": "INC-1"}})
    for line in tool_results(out):
        print(f"  {line}")
    print(f"\n  in thread state (files key): {list(out['files'])}")
    print(f"  on disk under {root}: {os.listdir(root)}")
    print(f"  in the Store: {[(item.namespace, item.key) for item in store.search(('oncall',))]}")

    print("\nthread INC-2 (new thread, same backend + store) looks around:")
    out = build_agent(ScriptedChatModel(replies=thread_2_script()), root, store).invoke(
        {"messages": [{"role": "user", "content": "What do you know?"}]}, {"configurable": {"thread_id": "INC-2"}})
    for path, line in zip(["ls /findings", "ls /workspace", "ls /memories", "read /memories/lessons.md"],
                          tool_results(out)):
        print(f"  {path:<26} → {line}")


if __name__ == "__main__":
    main()
