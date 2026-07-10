"""Token estimation used for chunk budgeting and context packing.

Phase 1 uses a fast, deterministic character-based heuristic rather than a
provider tokenizer: chunk budgets only need to be approximately right, and
keeping this dependency-free means the chunker has no network or model coupling.
A real tokenizer can be slotted in behind ``estimate_tokens`` later without
touching call sites (the eval harness in Phase 2 will tell us if it matters).
"""

# Empirically ~4 characters per token for source code across languages.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate the token count of ``text``."""
    return max(1, len(text) // _CHARS_PER_TOKEN)
