"""A chat model that replays canned replies and records what it was shown on every call.

Context engineering is about what the model sees, so this stand-in logs exactly that:
its label, the system prompt, the tool names and how many messages it got.
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
    tool_names: list[str] = []          # set by bind_tools on every model call
    seen: list[dict] = []               # one entry per call: what the model was shown

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.tool_names = [getattr(t, "name", None) or t["function"]["name"] for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = messages[0].content if messages and messages[0].type == "system" else ""
        self.seen.append({"model": self.label, "system": system, "tools": self.tool_names,
                          "messages": len(messages)})
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        # Fresh ids every reply: the agent treats a repeated tool_call id as already answered.
        tool_calls = [{**c, "id": f"{self.label}_{self.calls}_{i}"} for i, c in enumerate(reply.tool_calls)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(reply.content, tool_calls=tool_calls))])
