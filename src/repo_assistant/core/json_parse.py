"""Tolerant JSON-object extraction from LLM output.

Models occasionally wrap a JSON object in prose or a code fence despite
instructions. We slice from the first ``{`` to the last ``}`` and parse that,
returning ``None`` on failure so callers can retry or fall back. Shared by the
eval judge and the intent router.
"""

import json


def extract_json_object(text: str) -> dict | None:
    """Return the JSON object embedded in ``text``, or ``None`` if unparseable."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
