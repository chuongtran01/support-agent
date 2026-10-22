"""The OpenAI implementation of ``LLMClient``, on the Responses API.

It converts our messages and tools to the API's input items, and the API's stream events back
into ``TextDelta``, ``ToolCall`` and ``Done``.
"""

from collections.abc import AsyncIterator, Sequence

from openai import AsyncOpenAI, OpenAIError
from openai.types.responses import (
    FunctionToolParam,
    ResponseCompletedEvent,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseInputParam,
    ResponseOutputItemDoneEvent,
    ResponseStreamEvent,
    ResponseTextDeltaEvent,
)

from app.agent.llm import Done, LLMError, LLMEvent, TextDelta, ToolCall, Usage
from app.agent.messages import AssistantContent, MessageContent, ToolContent, UserContent
from app.config import settings
from app.tools.registry import ToolSchema


def to_responses_tools(tools: Sequence[ToolSchema]) -> list[FunctionToolParam]:
    """The tools in the shape the Responses API takes."""
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
        for tool in tools
    ]


def to_responses_input(messages: Sequence[MessageContent]) -> ResponseInputParam:
    """The conversation as Responses API input items.

    An assistant message becomes its text (if any) and one ``function_call`` item per tool
    call, and a tool message becomes the matching ``function_call_output``. The reasoning the
    model did on an earlier step is not sent back: the API accepts a conversation without it,
    and it is not stored.
    """
    items: ResponseInputParam = []
    for message in messages:
        match message:
            case UserContent():
                items.append({"role": "user", "content": message.content})
            case AssistantContent():
                if message.content:
                    items.append({"role": "assistant", "content": message.content})
                for call in message.tool_calls:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": call.id,
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        }
                    )
            case ToolContent():
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content,
                    }
                )
    return items


async def events_from_responses(
    stream: AsyncIterator[ResponseStreamEvent],
) -> AsyncIterator[LLMEvent]:
    """Turn Responses API stream events into ``LLMEvent``s.

    Text is sent as it arrives. A function call is whole by the time the API reports its item
    done, but is held back until the stream ends, so the loop sees the text first and then
    every call in the order the model made them. A failed response raises ``LLMError``.
    """
    calls: list[ToolCall] = []
    usage = Usage()
    async for event in stream:
        match event:
            case ResponseTextDeltaEvent(delta=text) if text:
                yield TextDelta(text)
            case ResponseOutputItemDoneEvent(item=ResponseFunctionToolCall() as item):
                calls.append(ToolCall(item.call_id, item.name, item.arguments))
            case (
                ResponseCompletedEvent(response=response)
                | ResponseIncompleteEvent(response=response)
            ):
                if response.usage:
                    usage = Usage(response.usage.input_tokens, response.usage.output_tokens)
            case ResponseFailedEvent(response=response):
                error = response.error
                raise LLMError(error.message if error else "the response failed")
            case ResponseErrorEvent(message=message):
                raise LLMError(message)
    for call in calls:
        yield call
    yield Done(usage)


class OpenAIClient:
    def __init__(self, api_key: str = "", model: str = ""):
        api_key = api_key or settings.openai_api_key
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set; add it to .env")
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model or settings.agent_model

    async def stream(
        self, system: str, messages: Sequence[MessageContent], tools: Sequence[ToolSchema]
    ) -> AsyncIterator[LLMEvent]:
        try:
            stream = await self._client.responses.create(
                model=self._model,
                instructions=system,
                input=to_responses_input(messages),
                tools=to_responses_tools(tools),
                reasoning={"effort": settings.agent_reasoning_effort},
                store=False,  # the conversation lives in our database, not OpenAI's
                stream=True,
            )
            async for event in events_from_responses(stream):
                yield event
        except OpenAIError as error:
            raise LLMError(str(error)) from error
