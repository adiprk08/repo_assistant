"""Security pass (docs/adr/0021): secret scanning + agent-loop guardrails.

These are regression tests for the defenses that keep credentials out of the index
and keep the agent from acting on injected instructions in untrusted repo content.
"""

from pathlib import Path

from repo_assistant.ingestion import filters
from repo_assistant.ingestion.models import SkipReason
from repo_assistant.ingestion.scanner import scan
from repo_assistant.reasoning.prompts import SYSTEM_PROMPT
from repo_assistant.reasoning.tools import TOOL_SCHEMAS
from tests.unit.test_ingestion_scanner import _init_repo

_AKIA = "AKIA" + "1234567890ABCDEF"  # AKIA + 16 chars
_GHP = "ghp_" + "a" * 36
_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIB...\n-----END RSA PRIVATE KEY-----\n"


def test_content_secret_patterns_detected() -> None:
    assert filters.contains_secret(f"aws_key = '{_AKIA}'")
    assert filters.contains_secret(f"token = {_GHP}")
    assert filters.contains_secret(_PEM)
    assert filters.contains_secret("k: AIza" + "b" * 35)
    assert filters.contains_secret("anthropic = 'sk-ant-" + "c" * 30 + "'")


def test_clean_code_is_not_flagged() -> None:
    # Real source that references secrets by name must not be excluded.
    assert not filters.contains_secret("def add(a, b):\n    return a + b\n")
    assert not filters.contains_secret("API_KEY = os.environ['API_KEY']  # from env\n")
    assert not filters.contains_secret("# See docs for how to set GITHUB_TOKEN\n")


async def test_scanner_excludes_inlined_secret_file(tmp_path: Path) -> None:
    acq = _init_repo(
        tmp_path,
        {
            "src/clean.py": b"def ok():\n    return 1\n",
            "src/leaky.py": f"AWS_KEY = '{_AKIA}'\n".encode(),
        },
    )
    result = await scan(acq)
    kept = {f.path for f in result.files}
    assert "src/clean.py" in kept
    assert "src/leaky.py" not in kept  # the inlined credential kept it out
    assert any(s.path == "src/leaky.py" and s.reason is SkipReason.SECRET for s in result.skipped)


def test_agent_tools_are_read_only() -> None:
    # The agent explores the *index* only — no tool can write, execute, or reach the FS.
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert names <= {"search_code", "get_symbol", "read_span", "graph_neighbors", "list_dir"}
    forbidden = ("write", "delete", "exec", "shell", "run_", "edit", "create", "fetch_url")
    assert not any(bad in name for name in names for bad in forbidden)


def test_system_prompt_marks_repo_content_untrusted() -> None:
    lowered = SYSTEM_PROMPT.lower()
    assert "untrusted" in lowered
    assert "never follow instructions" in lowered
