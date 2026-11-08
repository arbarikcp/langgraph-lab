"""Ch 17: why shallow loops fail. The same incident through a plain create_agent loop and a deep agent,
measuring how much context each model call had to carry.

Shallow: the 320 KB log lands in the message history and rides along on every later call.
Deep: the log is offloaded to a file (grep it), the table check runs in an isolated subagent,
the plan lives in `todos`, the report in `files`.
Run:  python -m chapters.ch17_deep_agents.shallow_vs_deep [--ollama]
"""
import sys

from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessage

from chapters.ch17_deep_agents.oncall_tools import table_health, taskmanager_logs
from chapters.ch17_deep_agents.sim.scripted_model import ScriptedChatModel, call

INCIDENT = "orders-enricher keeps restarting and the sales dashboards are stale. Find the root cause."
SYSTEM_PROMPT = "You are the streaming on-call engineer. Investigate, then write the RCA to /rca.md."
RCA = "# RCA\nenrich-join runs out of heap (OOM, heap=92%) → restarts → late data in sales.orders → stale dashboards."

ICEBERG_INVESTIGATOR = {
    "name": "iceberg-investigator",
    "description": "Checks Iceberg table health (snapshots, small files, freshness). Give it the table name.",
    "system_prompt": "You are an Iceberg specialist. Check the table and reply in one line.",
    "tools": [table_health],
}


def build_shallow(model):
    return create_agent(model, tools=[taskmanager_logs, table_health], system_prompt=SYSTEM_PROMPT)


def build_deep(model, investigator_model=None):
    investigator = {**ICEBERG_INVESTIGATOR, **({"model": investigator_model} if investigator_model else {})}
    return create_deep_agent(model=model, tools=[taskmanager_logs], system_prompt=SYSTEM_PROMPT,
                             middleware=[TodoListMiddleware()], subagents=[investigator])


def shallow_script() -> list[AIMessage]:
    return [AIMessage("", tool_calls=[call("taskmanager_logs", job="orders-enricher")]),
            AIMessage("", tool_calls=[call("table_health", table="sales.orders")]),
            AIMessage(RCA)]


def deep_scripts() -> tuple[list[AIMessage], list[AIMessage]]:
    """(main agent replies, iceberg-investigator replies)"""
    todos = [{"content": "Read orders-enricher logs", "status": "in_progress"},
             {"content": "Check sales.orders health", "status": "pending"},
             {"content": "Write /rca.md", "status": "pending"}]
    main = [AIMessage("", tool_calls=[call("write_todos", todos=todos)]),
            AIMessage("", tool_calls=[call("taskmanager_logs", job="orders-enricher")]),      # offloaded
            AIMessage("", tool_calls=[call("grep", pattern="ERROR", path="/large_tool_results",
                                           output_mode="content")]),
            AIMessage("", tool_calls=[call("task", subagent_type="iceberg-investigator",
                                           description="Check sales.orders health.")]),
            AIMessage("", tool_calls=[call("write_file", file_path="/rca.md", content=RCA)]),
            AIMessage("RCA written to /rca.md.")]
    sub = [AIMessage("", tool_calls=[call("table_health", table="sales.orders")]),
           AIMessage("sales.orders: newest data 47 min old; 1,240 snapshots, 38% small files.")]
    return main, sub


def context_per_call(model) -> list[int]:
    return [s["conversation_chars"] for s in model.seen]


def run(agent) -> dict:
    return agent.invoke({"messages": [{"role": "user", "content": INCIDENT}]}, {"recursion_limit": 30})


def main() -> None:
    if "--ollama" in sys.argv:
        from common.llm import llm
        for name, agent in (("shallow", build_shallow(llm)), ("deep", build_deep(llm))):
            out = run(agent)
            print(f"== {name}: {len(out['messages'])} messages, "
                  f"{sum(len(str(m.content)) for m in out['messages']):,} chars in state; "
                  f"files={list(out.get('files', {}))}\n{out['messages'][-1].content}\n")
        return

    shallow_model = ScriptedChatModel(label="shallow", replies=shallow_script())
    run(build_shallow(shallow_model))
    main_replies, sub_replies = deep_scripts()
    deep_model = ScriptedChatModel(label="deep", replies=main_replies)
    sub_model = ScriptedChatModel(label="sub", replies=sub_replies)
    out = run(build_deep(deep_model, sub_model))

    print("conversation chars the model carried, per call:")
    print(f"  shallow: {[f'{c:,}' for c in context_per_call(shallow_model)]}")
    print(f"  deep:    {[f'{c:,}' for c in context_per_call(deep_model)]}")
    print(f"  subagent:{[f'{c:,}' for c in context_per_call(sub_model)]}  (saw only its task description)")
    print(f"  total over the run: shallow {sum(context_per_call(shallow_model)):,} vs "
          f"deep {sum(context_per_call(deep_model)) + sum(context_per_call(sub_model)):,}")

    by_name = {m.name: m.content for m in out["messages"] if m.type == "tool"}
    print(f"\noffload  → {by_name['taskmanager_logs'].splitlines()[0][:110]}…")
    print(f"grep     → {by_name['grep'].splitlines()[1].strip()[:110]}")
    print(f"isolate  → task returned: {by_name['task']}")
    print(f"plan     → todos: {[t['content'] for t in out['todos']]}")
    print(f"files    → {list(out['files'])}")
    print(f"\n/rca.md:\n{out['files']['/rca.md']['content'] if isinstance(out['files']['/rca.md'], dict) else out['files']['/rca.md']}")


if __name__ == "__main__":
    main()
