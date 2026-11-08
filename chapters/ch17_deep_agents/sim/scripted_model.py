"""A chat model that replays canned replies and records how much context it was shown on every call.

Deep Agents exist to keep context small, so this stand-in logs exactly that: characters of
message content per call, and the tool names it was offered.
"""
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def call(name: str, **args) -> dict:
    """One tool call for a scripted reply (the model assigns the id when it replies)."""
    return {"name": name, "args": args, "id": ""}


def chars(message) -> int:
    content = message.content
    return len(content) if isinstance(content, str) else sum(len(str(part)) for part in content)


class ScriptedChatModel(BaseChatModel):
    label: str = "scripted"
    replies: list[AIMessage]
    calls: int = 0
    tool_names: list[str] = []
    tool_description_chars: int = 0
    seen: list[dict] = []               # per call: system prompt chars, conversation chars, messages

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.tool_names = [getattr(t, "name", None) or t.get("name") or t["function"]["name"] for t in tools]
        self.tool_description_chars = sum(len(getattr(t, "description", None) or t.get("description", ""))
                                          for t in tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = sum(chars(m) for m in messages if m.type == "system")
        self.seen.append({"system_chars": system, "conversation_chars": sum(chars(m) for m in messages) - system,
                          "messages": len(messages)})
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        # Fresh ids every reply: the agent treats a repeated tool_call id as already answered.
        tool_calls = [{**c, "id": f"{self.label}_{self.calls}_{i}"} for i, c in enumerate(reply.tool_calls)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(reply.content, tool_calls=tool_calls))])
