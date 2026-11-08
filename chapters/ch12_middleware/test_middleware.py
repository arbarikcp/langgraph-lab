"""Middleware behaviour with the scripted model: hook order, policy short-circuit, approval reject."""
from langgraph.types import Command

from chapters.ch12_middleware import custom_middleware as cm
from chapters.ch12_middleware.prod_agent import build_prod_agent, incident_script
from chapters.ch12_middleware.sim import lakehouse_ops
from chapters.ch12_middleware.sim.scripted_model import ScriptedChatModel


def test_hooks_nest_like_an_onion():
    trace: list[str] = []
    cm.run("trace", [cm.HookTracer("outer", trace), cm.HookTracer("inner", trace)], use_ollama=False)
    assert trace[:10] == ["outer.before_agent", "inner.before_agent", "outer.before_model", "inner.before_model",
                          "outer.wrap_model_call >", "inner.wrap_model_call >",
                          "inner.wrap_model_call <", "outer.wrap_model_call <",
                          "inner.after_model", "outer.after_model"]
    assert trace[-2:] == ["inner.after_agent", "outer.after_agent"]


def test_sql_policy_blocks_write_sql_and_audit_still_sees_it():
    cm.AUDIT.clear()
    cm.run("policy", [cm.audit_log, cm.sql_policy], use_ollama=False)
    assert lakehouse_ops.OPS.queries == ["SELECT * FROM sales.orders"]
    assert [a["status"] for a in cm.AUDIT] == ["error", "success"]


def test_rejected_restart_never_runs():
    lakehouse_ops.OPS = lakehouse_ops.LakehouseOps()
    agent = build_prod_agent(ScriptedChatModel(replies=incident_script()))
    cfg = {"configurable": {"thread_id": "t"}}
    assert "__interrupt__" in agent.invoke({"messages": [{"role": "user", "content": "restart it"}]}, cfg)
    agent.invoke(Command(resume={"decisions": [{"type": "reject", "message": "no"}]}), cfg)
    assert lakehouse_ops.OPS.restarts == []
