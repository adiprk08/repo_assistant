"""Intent router classification and path selection (no network)."""

from repo_assistant.core.interfaces import LLMClient, LLMResponse
from repo_assistant.reasoning.router import classify_intent


class _ScriptedLLM(LLMClient):
    def __init__(self, text: str) -> None:
        self._text = text

    async def generate(
        self, *, messages, system="", documents=None, tools=None, max_tokens=4096
    ) -> LLMResponse:
        return LLMResponse(text=self._text)


async def test_lookup_single_hop_takes_fast_path() -> None:
    llm = _ScriptedLLM('{"intent": "lookup", "multi_hop": false}')
    d = await classify_intent(llm, "Where is the retry backoff configured?")
    assert (d.intent, d.multi_hop, d.path) == ("lookup", False, "fast")


async def test_trace_intent_takes_agent_path() -> None:
    llm = _ScriptedLLM('{"intent": "trace", "multi_hop": true}')
    d = await classify_intent(llm, "Trace a request from main to the callback")
    assert d.path == "agent"


async def test_explain_but_multi_hop_takes_agent_path() -> None:
    # multi_hop overrides an otherwise-fast intent.
    llm = _ScriptedLLM('{"intent": "explain", "multi_hop": true}')
    d = await classify_intent(llm, "Explain how these two managers cooperate")
    assert d.path == "agent"


async def test_architecture_intent_is_agent_even_without_multi_hop_flag() -> None:
    llm = _ScriptedLLM('{"intent": "architecture", "multi_hop": false}')
    d = await classify_intent(llm, "How is the plugin system structured?")
    assert d.path == "agent"


async def test_unknown_intent_falls_back_to_other() -> None:
    llm = _ScriptedLLM('{"intent": "banana", "multi_hop": false}')
    d = await classify_intent(llm, "hi")
    assert d.intent == "other"
    assert d.path == "fast"


async def test_unparseable_router_output_defaults_to_agent() -> None:
    # An unparseable classification defaults to the agent path (correctness > cost).
    llm = _ScriptedLLM("I think this is a lookup question, probably.")
    d = await classify_intent(llm, "anything")
    assert d.path == "agent"
    assert d.multi_hop is True
