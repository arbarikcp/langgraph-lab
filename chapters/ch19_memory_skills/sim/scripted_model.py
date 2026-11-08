"""A chat model that replays canned replies and records the system prompt it was shown on every call.

Memory and skills are prompt engineering done by middleware, so this stand-in keeps the exact
system prompt per call. Each agent and subagent gets its own instance.
"""
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def text(message) -> str:
    """Message content as text, whether a string or a list of content blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)


def call(name: str, **args) -> dict:
    """One tool call for a scripted reply (the model assigns the id when it replies)."""
    return {"name": name, "args": args, "id": ""}


class ScriptedChatModel(BaseChatModel):
    label: str = "scripted"
    replies: list[AIMessage]
    calls: int = 0
    system_prompts: list[str] = []      # per call: the full system prompt

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.system_prompts.append("\n".join(text(m) for m in messages if m.type == "system"))
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        # Fresh ids every reply: the agent treats a repeated tool_call id as already answered.
        tool_calls = [{**c, "id": f"{self.label}_{self.calls}_{i}"} for i, c in enumerate(reply.tool_calls)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(reply.content, tool_calls=tool_calls))])
