"""Ch 15: one small middleware per harness component. The model decides; these do everything else.

Each component reports what it did as a `custom` stream event {"component", "text"}, so a run
prints a harness log. That stream is itself the observability component.
"""
import re
import time
from typing import NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import HumanMessage, ToolMessage

WRITE_SQL = re.compile(r"\b(DROP|DELETE|TRUNCATE|ALTER|INSERT|UPDATE)\b", re.I)
NUMBER = re.compile(r"\d[\d,.]*%?")


class HarnessState(AgentState):
    model_calls: NotRequired[int]        # loop control
    verifications: NotRequired[int]      # verification rounds used
    recalled: NotRequired[list[str]]     # memory loaded at the start of the run


def emit(runtime, component: str, text: str) -> None:
    runtime.stream_writer({"component": component, "text": text})


def blocked(tool_call: dict, text: str) -> ToolMessage:
    return ToolMessage(text, tool_call_id=tool_call["id"], name=tool_call["name"], status="error")


class Memory(AgentMiddleware):
    """State and memory: recall past resolutions from the Store at the start, save this one at the end."""

    state_schema = HarnessState
    NAMESPACE = ("resolutions",)

    def before_agent(self, state, runtime):
        recalled = [item.value["summary"] for item in runtime.store.search(self.NAMESPACE)]
        emit(runtime, "memory", f"recalled {len(recalled)} past resolution(s) from the Store")
        return {"recalled": recalled}

    def after_agent(self, state, runtime):
        thread = runtime.execution_info.thread_id
        runtime.store.put(self.NAMESPACE, thread, {"summary": state["messages"][-1].content})
        emit(runtime, "memory", f"saved this resolution under {self.NAMESPACE + (thread,)}")


class LoopControl(AgentMiddleware):
    """Loop control: a hard cap on model calls, counted in state (so it survives resume)."""

    state_schema = HarnessState

    def __init__(self, max_model_calls: int):
        super().__init__()
        self.max_model_calls = max_model_calls

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        used = state.get("model_calls", 0)
        if used >= self.max_model_calls:
            emit(runtime, "loop control", f"cap of {self.max_model_calls} model calls reached → end")
            return {"jump_to": "end"}
        emit(runtime, "loop control", f"model call {used + 1}/{self.max_model_calls}")
        return {"model_calls": used + 1}


class ContextManager(AgentMiddleware):
    """Context management: the model sees recalled memory and only the newest tool outputs.
    State keeps everything; only this call's view is trimmed."""

    def __init__(self, keep_tool_outputs: int):
        super().__init__()
        self.keep = keep_tool_outputs

    def wrap_model_call(self, request, handler):
        tool_ids = [i for i, m in enumerate(request.messages) if m.type == "tool"]
        old = set(tool_ids[:-self.keep]) if len(tool_ids) > self.keep else set()
        messages = [m.model_copy(update={"content": f"[cleared: {len(m.content)} chars]"}) if i in old else m
                    for i, m in enumerate(request.messages)]
        prompt = request.system_message.content if request.system_message else ""
        if recalled := request.state.get("recalled"):
            prompt += "\nPast resolutions:\n- " + "\n- ".join(recalled)
        if old:
            emit(request.runtime, "context", f"cleared {len(old)} old tool output(s) from the model's view")
        return handler(request.override(messages=messages, system_prompt=prompt))


class ActionSpace(AgentMiddleware):
    """Action space: which tools this call may use (here: all, reported once per run)."""

    def wrap_model_call(self, request, handler):
        if request.state.get("model_calls", 0) == 1:
            emit(request.runtime, "action space", f"tools: {[t.name for t in request.tools]}")
        return handler(request)


class AuditTrail(AgentMiddleware):
    """Observability: one record per tool call, blocked ones included (it is the outermost tool wrapper)."""

    def wrap_tool_call(self, request, handler):
        start = time.perf_counter()
        result = handler(request)
        ms = (time.perf_counter() - start) * 1000
        emit(request.runtime, "observability",
             f"{request.tool_call['name']}({request.tool_call['args']}) → {result.status} in {ms:.1f} ms")
        return result


class SqlGuardrail(AgentMiddleware):
    """Guardrails and policy: write SQL never reaches Trino."""

    def wrap_tool_call(self, request, handler):
        if WRITE_SQL.search(request.tool_call["args"].get("sql", "")):
            emit(request.runtime, "guardrail", "write SQL blocked")
            return blocked(request.tool_call, "BLOCKED by policy: read-only SQL only.")
        return handler(request)


class QueryBudget(AgentMiddleware):
    """Budgets: at most N Trino queries per thread, counted from the messages in state."""

    def __init__(self, max_queries: int):
        super().__init__()
        self.max_queries = max_queries

    def wrap_tool_call(self, request, handler):
        if request.tool_call["name"] == "run_trino_query":
            used = sum(1 for m in request.state["messages"] if m.type == "tool" and m.name == "run_trino_query")
            if used >= self.max_queries:
                emit(request.runtime, "budget", f"Trino budget {self.max_queries} used up → refused")
                return blocked(request.tool_call, f"Budget exhausted: {self.max_queries} queries. Answer now.")
        return handler(request)


class Resilience(AgentMiddleware):
    """Resilience: retry a failing tool call, then report the error to the model instead of crashing."""

    def __init__(self, retries: int):
        super().__init__()
        self.retries = retries

    def wrap_tool_call(self, request, handler):
        for attempt in range(1, self.retries + 2):
            try:
                return handler(request)
            except ConnectionError as err:
                then = "retrying" if attempt <= self.retries else "giving up, telling the model"
                emit(request.runtime, "resilience", f"attempt {attempt} failed ({err}); {then}")
        return blocked(request.tool_call, f"Tool failed after {self.retries + 1} attempts.")


class Verifier(AgentMiddleware):
    """Verification: a final answer must quote at least one figure from the evidence; otherwise send it back."""

    state_schema = HarnessState

    def __init__(self, max_rounds: int):
        super().__init__()
        self.max_rounds = max_rounds

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):
        answer = state["messages"][-1]
        if answer.tool_calls:
            return None
        evidence = " ".join([m.content for m in state["messages"] if m.type == "tool"] + state.get("recalled", []))
        cited = [n for n in NUMBER.findall(answer.content) if n in evidence]
        rounds = state.get("verifications", 0)
        if cited or rounds >= self.max_rounds:
            emit(runtime, "verification", f"accepted (cites {cited})" if cited else "gave up: accepted unverified")
            return None
        emit(runtime, "verification", "rejected: no figure from the evidence → back to the model")
        feedback = HumanMessage("Verifier: cite the figures from the tool results that support your answer.")
        return {"messages": [feedback], "verifications": rounds + 1, "jump_to": "model"}
