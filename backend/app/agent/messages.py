"""The content of a stored chat message.

``chat_messages.content`` holds everything about a message except its role, in the shape of
the OpenAI message (``as_json``), so a stored message is replayed to the model as it was.
The models reject unknown fields, so a row that does not match is an error when it is loaded.
"""

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter


class _Content(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: ClassVar[str]

    def as_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def as_openai(self) -> dict[str, Any]:
        return {"role": self.role, **self.as_json()}


class UserContent(_Content):
    role: ClassVar[str] = "user"

    content: str


class FunctionCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    arguments: str  # the JSON text exactly as the model wrote it


class StoredToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class AssistantContent(_Content):
    role: ClassVar[str] = "assistant"

    # None when the model went straight to a tool call, which the API stores as null
    content: str | None
    tool_calls: tuple[StoredToolCall, ...] = ()

    def as_json(self) -> dict[str, Any]:
        data = super().as_json()
        if not self.tool_calls:
            del data["tool_calls"]  # the API rejects an empty list
        return data


class ToolContent(_Content):
    role: ClassVar[str] = "tool"

    tool_call_id: str
    content: str  # the JSON text the tool returned


MessageContent = UserContent | AssistantContent | ToolContent

_BY_ROLE: dict[str, TypeAdapter[Any]] = {
    cls.role: TypeAdapter(cls) for cls in (UserContent, AssistantContent, ToolContent)
}


def parse_content(role: str, raw: dict[str, Any]) -> MessageContent:
    """Read a stored row. Raises ``ValidationError`` if it does not fit its role."""
    return _BY_ROLE[role].validate_python(raw)
