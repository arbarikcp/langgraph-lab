"""A chat model that replays canned replies, so the harness runs without Ollama.

A reply may be an Exception (a flaky model API: the call raises), and every reply
carries token usage, so the cost budget has something to count.
"""
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def call(name: str, **args) -> dict:
    """One tool call for a scripted reply (the model assigns the id when it replies)."""
    return {"name": name, "args": args, "id": ""}


class ScriptedChatModel(BaseChatModel):
    replies: list[Any]                  # AIMessage, or an Exception to raise
    calls: int = 0
    input_tokens: int = 2_000           # pretend usage per call
    output_tokens: int = 200

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        # Fresh ids every reply: the agent treats a repeated tool_call id as already answered.
        tool_calls = [{**c, "id": f"call_{self.calls}_{i}"} for i, c in enumerate(reply.tool_calls)]
        usage = {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                 "total_tokens": self.input_tokens + self.output_tokens}
        message = AIMessage(reply.content, tool_calls=tool_calls, usage_metadata=usage)
        return ChatResult(generations=[ChatGeneration(message=message)])
