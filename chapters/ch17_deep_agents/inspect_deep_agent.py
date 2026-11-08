"""Ch 17: build a minimal deep agent and print what it is made of — middleware, tools, graph, state.

create_deep_agent is create_agent + a fixed middleware stack; we record that stack by watching the
create_agent call it makes. No model call is needed except one, to see which tools the model is offered.
Run:  python -m chapters.ch17_deep_agents.inspect_deep_agent
"""
from importlib.metadata import version
from unittest import mock

import deepagents.graph
from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessage

from chapters.ch17_deep_agents.oncall_tools import taskmanager_logs
from chapters.ch17_deep_agents.sim.scripted_model import ScriptedChatModel

SYSTEM_PROMPT = "You are the streaming on-call engineer."


def build_minimal(model):
    """Planning is opt-in on deepagents 0.7+: add TodoListMiddleware yourself."""
    return create_deep_agent(model=model, tools=[taskmanager_logs], system_prompt=SYSTEM_PROMPT,
                             middleware=[TodoListMiddleware()])


def recorded_stack(model) -> tuple[object, dict]:
    """Build the agent while recording the arguments create_deep_agent passes to create_agent."""
    real = deepagents.graph.create_agent
    seen: dict = {}

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    with mock.patch.object(deepagents.graph, "create_agent", spy):
        agent = build_minimal(model)
    return agent, seen


def main() -> None:
    print(f"versions: deepagents {version('deepagents')}, langchain {version('langchain')}, "
          f"langgraph {version('langgraph')}\n")
    model = ScriptedChatModel(replies=[AIMessage("ok")])
    agent, kwargs = recorded_stack(model)

    print("middleware stack (outer → inner), as passed to create_agent:")
    for i, mw in enumerate(kwargs["middleware"], 1):
        print(f"  {i:>2}. {mw.name}")

    agent.invoke({"messages": [{"role": "user", "content": "hi"}]})
    registered = list(agent.nodes["tools"].bound.tools_by_name)
    print(f"\ntools registered in the tools node: {registered}")
    print(f"tools offered to the model:         {model.tool_names}")
    print(f"  → not offered: {sorted(set(registered) - set(model.tool_names))} (no sandbox backend)")

    print(f"\nharness instructions the model gets: system prompt {model.seen[0]['system_chars']} chars "
          f"(yours: {len(SYSTEM_PROMPT)}) + tool descriptions {model.tool_description_chars} chars")
    print(f"state keys: {list(kwargs['state_schema'].__annotations__)} + middleware keys "
          f"{sorted({k for mw in kwargs['middleware'] if mw.state_schema for k in mw.state_schema.__annotations__} - {'messages', 'jump_to', 'structured_response'})}")
    print(f"graph nodes: {[n for n in agent.get_graph().nodes if not n.startswith('__')]}")
    print(f"default recursion_limit: {agent.config['recursion_limit']}")


if __name__ == "__main__":
    main()
