"""Context isolation (supervisor) vs shared history (handoff), plus the router's dispatch. Scripted models."""
from chapters.ch14_multi_agent.handoff import build_handoff_graph
from chapters.ch14_multi_agent.router import classify
from chapters.ch14_multi_agent.sim import scripts
from chapters.ch14_multi_agent.sim.scripted_model import ScriptedChatModel
from chapters.ch14_multi_agent.specialists import INCIDENT
from chapters.ch14_multi_agent.supervisor import build_supervisor

USER = {"messages": [{"role": "user", "content": INCIDENT}]}


def test_supervisor_isolates_specialist_context():
    sup = ScriptedChatModel(label="supervisor", replies=scripts.supervisor_replies())
    flink = ScriptedChatModel(label="flink", replies=scripts.flink_replies())
    iceberg = ScriptedChatModel(label="iceberg", replies=scripts.iceberg_replies())
    out = build_supervisor(sup, flink, iceberg, delegations=[]).invoke(USER)

    assert flink.seen[0]["first_user"].startswith("Is orders-enricher healthy")      # not the user's words
    assert [m.type for m in out["messages"]] == ["human", "ai", "tool", "tool", "ai"]  # only summaries came back


def test_handoff_shares_history_and_keeps_the_thread():
    triage = ScriptedChatModel(label="triage", replies=scripts.triage_replies())
    flink = ScriptedChatModel(label="flink", replies=scripts.flink_replies(follow_up=True))
    graph = build_handoff_graph(triage, flink)
    cfg = {"configurable": {"thread_id": "t"}}
    graph.invoke(USER, cfg)
    out = graph.invoke({"messages": [{"role": "user", "content": "What should I change?"}]}, cfg)

    assert flink.seen[0]["first_user"] == INCIDENT
    assert out["active_agent"] == "flink_agent" and triage.calls == 1


def test_router_dispatches_on_keywords():
    assert classify({"question": INCIDENT})["routes"] == ["flink_agent", "iceberg_agent"]
    assert classify({"question": "Who is on call?"})["routes"] == []
