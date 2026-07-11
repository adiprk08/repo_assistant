"""Anthropic LLM adapter with native citations (docs/adr/0007).

Retrieved chunks are passed as ``document`` content blocks with citations
enabled; the API returns char-anchored citations that map deterministically back
to a document (and thus to ``path:lines@commit`` after verification). Tool-use
blocks are surfaced for the agentic reasoning path (Phase 3).
"""

from typing import Any

from anthropic import APIError, AsyncAnthropic

from repo_assistant.core.errors import ProviderError
from repo_assistant.core.interfaces import (
    Citation,
    Document,
    LLMClient,
    LLMResponse,
    Message,
    ToolCall,
    ToolResult,
    Usage,
)
from repo_assistant.core.logging import get_logger

logger = get_logger(__name__)


def _document_block(doc: Document) -> dict[str, Any]:
    return {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": doc.content},
        "title": doc.title,
        "citations": {"enabled": True},
    }


def _tool_use_block(call: ToolCall) -> dict[str, Any]:
    return {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}


def _tool_result_block(result: ToolResult) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": result.tool_use_id,
        "content": result.content,
        "is_error": result.is_error,
    }


def _render_turn(message: Message) -> dict[str, Any]:
    """Render one agentic-loop turn (tool_use on assistant, tool_result on user)."""
    content: list[dict[str, Any]] = []
    if message.content:
        content.append({"type": "text", "text": message.content})
    content.extend(_tool_use_block(c) for c in message.tool_calls)
    content.extend(_tool_result_block(r) for r in message.tool_results)
    return {"role": message.role, "content": content}


def _build_messages(messages: list[Message], documents: list[Document]) -> list[dict[str, Any]]:
    """Render conversation turns to the API shape.

    On the fast path each turn is plain text and documents attach to the final
    user turn so citations anchor to the question. Agentic-loop turns carry
    ``tool_calls``/``tool_results`` and render to structured content blocks.
    """
    if not messages:
        raise ProviderError("generate() requires at least one message")

    api_messages: list[dict[str, Any]] = []
    for m in messages[:-1]:
        if m.tool_calls or m.tool_results:
            api_messages.append(_render_turn(m))
        else:
            api_messages.append({"role": m.role, "content": m.content})

    last = messages[-1]
    if last.tool_calls or last.tool_results:
        api_messages.append(_render_turn(last))
        return api_messages

    content: list[dict[str, Any]] = [_document_block(d) for d in documents]
    content.append({"type": "text", "text": last.content})
    api_messages.append({"role": last.role, "content": content})
    return api_messages


_CACHE_CONTROL = {"type": "ephemeral"}


def _cached_system(system: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": system, "cache_control": _CACHE_CONTROL}]


def _cached_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark the last tool so the (constant) tool-schema prefix is cached."""
    cached = [dict(t) for t in tools]
    cached[-1] = {**cached[-1], "cache_control": _CACHE_CONTROL}
    return cached


def _cache_message_prefix(api_messages: list[dict[str, Any]]) -> None:
    """Cache-mark the last content block so the growing message prefix is reused
    on the next agent turn (Anthropic caches everything up to the marked block)."""
    if not api_messages:
        return
    content = api_messages[-1]["content"]
    if isinstance(content, str):
        api_messages[-1]["content"] = [
            {"type": "text", "text": content, "cache_control": _CACHE_CONTROL}
        ]
    elif content:
        content[-1] = {**content[-1], "cache_control": _CACHE_CONTROL}


class AnthropicLLMClient(LLMClient):
    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(
        self,
        *,
        messages: list[Message],
        system: str = "",
        documents: list[Document] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        # Build kwargs so optional params are simply absent when unset; this also
        # keeps us clear of the SDK's precise TypedDict overloads for dict payloads.
        api_messages = _build_messages(messages, documents or [])
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": api_messages,
        }
        # Prompt caching is worthwhile only for the agentic loop, where the system
        # prompt + tool schemas are constant and the context accumulates across
        # calls (the fast path and judge are single-shot). Presence of `tools` is
        # exactly that signal, so we scope caching to it (docs/adr/0007).
        if tools:
            if system:
                kwargs["system"] = _cached_system(system)
            kwargs["tools"] = _cached_tools(tools)
            _cache_message_prefix(api_messages)
        else:
            if system:
                kwargs["system"] = system

        try:
            response = await self._client.messages.create(**kwargs)
        except APIError as exc:
            raise ProviderError(f"Anthropic generation failed: {exc}") from exc

        return _parse_response(response)


def _parse_response(response: Any) -> LLMResponse:
    text_parts: list[str] = []
    citations: list[Citation] = []
    tool_calls: list[ToolCall] = []

    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
            for citation in block.citations or []:
                if citation.type == "char_location":
                    citations.append(
                        Citation(
                            document_index=citation.document_index,
                            start_char=citation.start_char_index,
                            end_char=citation.end_char_index,
                            cited_text=citation.cited_text,
                        )
                    )
        elif block.type == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))

    usage = response.usage
    return LLMResponse(
        text="".join(text_parts),
        citations=tuple(citations),
        tool_calls=tuple(tool_calls),
        usage=Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
        ),
        stop_reason=response.stop_reason or "end_turn",
    )
