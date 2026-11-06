"""Ch 11: the Ch 4 loop as one factory call — create_agent returns a compiled LangGraph graph.

Run (needs Ollama):  python -m chapters.ch11_create_agent.flink_oncall_agent
"""
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

from chapters.ch11_create_agent.oncall_tools import TOOLS, OnCallContext, OnCallState
from common.llm import MODEL, llm

SYSTEM_PROMPT = (
    "You are the streaming on-call assistant. First list the caller's jobs with my_jobs, "
    "then check each job's status and metrics. Call flag_suspect for every unhealthy job. "
    "Finish with a short diagnosis and one recommended fix."
)


def build_agent():
    return create_agent(
        model=llm,                      # a configured model instance (or a string like "ollama:qwen3.5:4b")
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        state_schema=OnCallState,       # adds `suspects` to the agent's state
        context_schema=OnCallContext,
        checkpointer=InMemorySaver(),
        name="flink_oncall",
    )


def main() -> None:
    agent = build_agent()
    print(f"model: {MODEL}   graph: {type(agent).__name__} nodes={list(agent.get_graph().nodes)}\n")
    cfg = {"configurable": {"thread_id": "u42-s1"}}
    ctx = OnCallContext(user_id="u42", team="streaming")

    question = {"messages": [{"role": "user", "content": "Are any of my jobs unhealthy?"}]}
    for update in agent.stream(question, {**cfg, "recursion_limit": 20}, context=ctx, stream_mode="updates"):
        for node, delta in update.items():
            for msg in (delta or {}).get("messages", []):
                if getattr(msg, "tool_calls", None):
                    print(f"[{node}] calls: {[f'{c['name']}({c['args']})' for c in msg.tool_calls]}")
                elif msg.type == "tool":
                    print(f"[{node}] {msg.name} -> {msg.content}")
                else:
                    print(f"[{node}] answer:\n{msg.content}")

    print(f"\nstate['suspects'] (written by a tool via Command): {agent.get_state(cfg).values.get('suspects')}")

    follow_up = {"messages": [{"role": "user", "content": "Which jobs did you flag, and why? One line each."}]}
    print("\nfollow-up on the same thread:\n" + agent.invoke(follow_up, cfg, context=ctx)["messages"][-1].content)


if __name__ == "__main__":
    main()
