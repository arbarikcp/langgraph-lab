"""What the model sees vs what is stored, with scripted models (no Ollama)."""
from langchain_core.messages import AIMessage

from chapters.ch13_context_output import triage_report as tr
from chapters.ch13_context_output.sim import lakehouse
from chapters.ch13_context_output.sim.scripted_model import ScriptedChatModel, call
from chapters.ch13_context_output.triage_tools import TriageContext

QUESTION = {"messages": [{"role": "user", "content": tr.QUESTION}]}


def test_per_call_view_escalation_and_validated_report():
    small_replies, big_replies = tr.scripts("analytics")
    small = ScriptedChatModel(label="small", replies=small_replies)
    big = ScriptedChatModel(label="big", replies=big_replies)
    out = tr.build_agent(small, big).invoke(QUESTION, context=TriageContext("u7", "analytics"))

    assert [s["model"] for s in small.seen + big.seen] == ["small", "small", "big", "big"]
    assert all("restart_job" not in s["tools"] for s in small.seen + big.seen)
    assert "analytics" in small.seen[0]["system"]
    assert all(m.type != "system" for m in out["messages"])          # the prompt is never stored
    assert out["structured_response"].severity == "P2"                # the invalid "P0" was retried


def test_hidden_tool_is_also_refused():
    lakehouse.LAKEHOUSE = lakehouse.Lakehouse()
    rogue = ScriptedChatModel(replies=[
        AIMessage("", tool_calls=[call("restart_job", job="orders-enricher", parallelism=8)]),
        AIMessage("", tool_calls=[call("IncidentReport", **tr.GOOD_REPORT)])])
    out = tr.build_agent(rogue, rogue).invoke(QUESTION, context=TriageContext("u7", "analytics"))
    assert lakehouse.LAKEHOUSE.restarts == []
    assert out["messages"][2].content.startswith("DENIED")
