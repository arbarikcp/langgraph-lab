"""What a deep agent is made of, and that offload + isolation keep the context small. Scripted models."""
from langchain_core.messages import AIMessage

from chapters.ch17_deep_agents import inspect_deep_agent as ida
from chapters.ch17_deep_agents import shallow_vs_deep as sd
from chapters.ch17_deep_agents.sim.scripted_model import ScriptedChatModel


def test_stack_and_offered_tools():
    model = ScriptedChatModel(replies=[AIMessage("ok")])
    agent, kwargs = ida.recorded_stack(model)
    names = [mw.name for mw in kwargs["middleware"]]
    assert {"FilesystemMiddleware", "SubAgentMiddleware", "SummarizationMiddleware", "TodoListMiddleware"} <= set(names)
    agent.invoke({"messages": [{"role": "user", "content": "hi"}]})
    assert {"task", "write_todos", "grep"} <= set(model.tool_names) and "execute" not in model.tool_names


def test_offload_and_isolation_keep_context_small():
    shallow = ScriptedChatModel(replies=sd.shallow_script())
    sd.run(sd.build_shallow(shallow))
    main_replies, sub_replies = sd.deep_scripts()
    deep, sub = ScriptedChatModel(replies=main_replies), ScriptedChatModel(replies=sub_replies)
    out = sd.run(sd.build_deep(deep, sub))

    assert max(sd.context_per_call(shallow)) > 300_000
    assert max(sd.context_per_call(deep)) < 5_000
    assert sd.context_per_call(sub)[0] < 100                            # only its task description
    assert "/rca.md" in out["files"] and len(out["todos"]) == 3
