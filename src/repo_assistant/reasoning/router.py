"""Intent router: classify a question and choose a reasoning path (ADR-0006).

A cheap model (claude-haiku-4-5) labels the question's intent and whether it
likely needs multiple hops of evidence. Single-hop intents take the fast path
(one retrieval + generation); multi-hop intents take the budgeted agentic loop.
The router is a new failure mode, so its accuracy is a tracked eval metric with a
safe default: when uncertain, prefer the agent path (correctness over cost).
"""

from dataclasses import dataclass
from typing import Literal, cast

from repo_assistant.core.interfaces import LLMClient, Message
from repo_assistant.core.json_parse import extract_json_object
from repo_assistant.core.logging import get_logger

logger = get_logger(__name__)

Intent = Literal["lookup", "explain", "architecture", "trace", "debug", "other"]
Path = Literal["fast", "agent"]

_INTENTS: frozenset[str] = frozenset(
    {"lookup", "explain", "architecture", "trace", "debug", "other"}
)
# Intents whose evidence is inherently multi-file / multi-hop take the agent path.
_AGENT_INTENTS: frozenset[str] = frozenset({"architecture", "trace", "debug"})
_ROUTER_MAX_TOKENS = 128

_ROUTER_SYSTEM = """\
You route a question about a code repository to one of two answering strategies.
Classify the question's intent and whether answering it needs evidence from
multiple places in the codebase (multiple functions/files, or following a call
chain).

intent is one of:
- lookup: find one specific thing (a config value, where something is defined).
- explain: explain what one function/class/module does.
- architecture: how a subsystem is structured or how components fit together.
- trace: follow a flow across calls/files (e.g. request to response, A calls B calls C).
- debug: why something fails or behaves unexpectedly.
- other: greetings, meta questions, anything else.

Respond with ONLY a JSON object, no prose:
{"intent": "<intent>", "multi_hop": <true|false>}\
"""


@dataclass(frozen=True, slots=True)
class RouterDecision:
    intent: Intent
    multi_hop: bool
    path: Path


def _decide_path(intent: str, multi_hop: bool) -> Path:
    return "agent" if multi_hop or intent in _AGENT_INTENTS else "fast"


async def classify_intent(llm: LLMClient, question: str) -> RouterDecision:
    """Classify ``question`` and pick a reasoning path. Defaults to the agent path
    when the router output can't be parsed (correctness over cost, per ADR-0006)."""
    response = await llm.generate(
        messages=[Message(role="user", content=f"Question:\n{question}")],
        system=_ROUTER_SYSTEM,
        max_tokens=_ROUTER_MAX_TOKENS,
    )
    data = extract_json_object(response.text.strip())
    if data is None:
        logger.warning("router output unparseable; defaulting to agent", text=response.text[:120])
        return RouterDecision(intent="other", multi_hop=True, path="agent")

    raw_intent = str(data.get("intent", "other")).lower()
    intent: Intent = cast(Intent, raw_intent) if raw_intent in _INTENTS else "other"
    multi_hop = bool(data.get("multi_hop", False))
    decision = RouterDecision(
        intent=intent, multi_hop=multi_hop, path=_decide_path(intent, multi_hop)
    )
    logger.info("router decision", intent=decision.intent, multi_hop=multi_hop, path=decision.path)
    return decision
