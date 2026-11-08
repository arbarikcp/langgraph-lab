"""A chat model that replays canned replies and records how much context it was shown on every call.

Each agent and subagent in the demos gets its own instance, so their scripts never interleave.
"""
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def call(name: str, **args) -> dict:
    """One tool call for a scripted reply (the model assigns the id when it replies)."""
    return {"name": name, "args": args, "id": ""}


class ScriptedChatModel(BaseChatModel):
    label: str = "scripted"
    replies: list[AIMessage]
    calls: int = 0
    seen: list[int] = []                # per call: chars of non-system message content

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(sum(len(str(m.content)) for m in messages if m.type != "system"))
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        # Fresh ids every reply: the agent treats a repeated tool_call id as already answered.
        tool_calls = [{**c, "id": f"{self.label}_{self.calls}_{i}"} for i, c in enumerate(reply.tool_calls)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(reply.content, tool_calls=tool_calls))])
