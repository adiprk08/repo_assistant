"""Provider interfaces.

Pipeline code depends only on these abstractions, never on vendor SDKs
directly (see docs/adr/0001-language-and-stack.md). Each interface has a
fake in-memory implementation in `repo_assistant.core.fakes` so pipelines
are unit-testable without network access or infrastructure.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class Message:
    """A conversation turn.

    Fast-path turns carry plain ``content``. The agentic loop additionally uses
    ``tool_calls`` on an assistant turn (the tools it requested) and
    ``tool_results`` on the following user turn (the outputs fed back). Both
    default empty, so the single-pass path is untouched.
    """

    role: Literal["user", "assistant"]
    content: str
    tool_calls: tuple["ToolCall", ...] = ()
    tool_results: tuple["ToolResult", ...] = ()


@dataclass(frozen=True, slots=True)
class Citation:
    document_index: int
    start_char: int
    end_char: int
    cited_text: str


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    citations: tuple[Citation, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    stop_reason: str = "end_turn"


@dataclass(frozen=True, slots=True)
class Document:
    """A retrieved chunk passed to the LLM as a citable document."""

    id: str
    title: str
    content: str


class LLMClient(ABC):
    """A chat-completion provider supporting grounded generation, tool use, and citations."""

    @abstractmethod
    async def generate(
        self,
        *,
        messages: list[Message],
        system: str = "",
        documents: list[Document] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...


InputType = Literal["document", "query"]


class Embedder(ABC):
    """A text embedding provider.

    ``input_type`` lets asymmetric embedders (e.g. voyage-code-3) encode corpus
    chunks and search queries differently, which improves retrieval. Symmetric
    embedders may ignore it.
    """

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def dimensions(self) -> int: ...

    @abstractmethod
    async def embed(
        self, texts: list[str], *, input_type: InputType = "document"
    ) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class RerankResult:
    index: int
    score: float


class Reranker(ABC):
    """A cross-encoder reranking provider."""

    @abstractmethod
    async def rerank(
        self, *, query: str, documents: list[str], top_k: int
    ) -> list[RerankResult]: ...


@dataclass(frozen=True, slots=True)
class VectorPoint:
    id: str
    dense_vector: list[float]
    sparse_vector: dict[int, float] | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SearchResult:
    id: str
    score: float
    payload: dict[str, Any]


class VectorIndex(ABC):
    """A hybrid dense+sparse vector store, partitioned by repo_id payload."""

    async def prepare(self, dimensions: int) -> None:
        """One-time setup (e.g. create the collection). Default: no-op."""
        return None

    @abstractmethod
    async def upsert(self, points: list[VectorPoint]) -> None: ...

    @abstractmethod
    async def query(
        self,
        *,
        repo_id: str,
        dense_vector: list[float],
        sparse_vector: dict[int, float] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]: ...

    async def query_sparse(
        self,
        *,
        repo_id: str,
        sparse_vector: dict[int, float],
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Query the sparse (BM25) vector. Default: no-op for indexes without sparse."""
        return []

    @abstractmethod
    async def fetch(self, *, repo_id: str, ids: list[str]) -> list[SearchResult]: ...

    @abstractmethod
    async def delete(self, *, repo_id: str, ids: list[str]) -> None: ...
