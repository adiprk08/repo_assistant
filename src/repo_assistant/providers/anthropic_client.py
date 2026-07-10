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


def _build_messages(messages: list[Message], documents: list[Document]) -> list[dict[str, Any]]:
    """Render conversation turns to the API shape, attaching documents to the
    final user turn so citations anchor to the question being answered."""
    if not messages:
        raise ProviderError("generate() requires at least one message")

    api_messages: list[dict[str, Any]] = [
        {"role": m.role, "content": m.content} for m in messages[:-1]
    ]
    last = messages[-1]
    content: list[dict[str, Any]] = [_document_block(d) for d in documents]
    content.append({"type": "text", "text": last.content})
    api_messages.append({"role": last.role, "content": content})
    return api_messages


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
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": _build_messages(messages, documents or []),
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

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
