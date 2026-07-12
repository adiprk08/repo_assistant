"""Pure exclusion-policy predicates for the scanner.

Repository content is untrusted input (docs/RISKS.md #4). The scanner's job is to
keep binaries, vendored/generated bulk, oversized files, and — critically —
secrets out of the index and therefore out of prompts. These predicates are kept
free of I/O so they can be unit-tested exhaustively against path/byte fixtures.
"""

import re

# Max indexable file size. Larger files are almost always data/generated blobs;
# real source files comfortably fit (see docs/ARCHITECTURE.md §4).
MAX_FILE_BYTES = 1_000_000

# Directory names that never contain first-party source worth indexing.
_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "bower_components",
        "vendor",
        "venv",
        ".venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "target",
        "out",
        ".next",
        ".nuxt",
        ".svelte-kit",
        "site-packages",
        ".idea",
        ".vscode",
        "coverage",
        "htmlcov",
        ".terraform",
    }
)

# Exact filenames that are generated or otherwise not worth embedding.
_GENERATED_FILENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
    }
)

# Filename suffixes indicating minified/generated/map artifacts.
_GENERATED_SUFFIXES: tuple[str, ...] = (
    ".min.js",
    ".min.css",
    ".map",
    ".bundle.js",
    ".d.ts",
)

# Filenames/suffixes that commonly hold secrets. Excluded outright.
_SECRET_FILENAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "credentials",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        ".netrc",
        ".pgpass",
    }
)

_SECRET_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".keystore",
    ".jks",
)


# High-confidence credential patterns: a file whose *content* matches is kept out
# of the index entirely (docs/adr/0021). Deliberately low false-positive — provider
# key prefixes and PEM private-key headers, not generic high-entropy strings.
_SECRET_CONTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"),  # GitHub PAT / OAuth / app tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),  # GitHub fine-grained PAT
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),  # Anthropic API key
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),  # Google API key
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),  # Slack token
    re.compile(r"\bsk_live_[0-9A-Za-z]{24,}\b"),  # Stripe live secret key
    re.compile(r"\bglpat-[0-9A-Za-z_\-]{20,}\b"),  # GitLab PAT
)


def contains_secret(text: str) -> bool:
    """True if ``text`` embeds a high-confidence hardcoded credential.

    Used to keep a source/config file with an inlined key (e.g. ``AKIA…`` or a PEM
    private key) out of the index even when its *name* looks innocuous.
    """
    return any(pattern.search(text) for pattern in _SECRET_CONTENT_PATTERNS)


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def is_excluded_dir(name: str) -> bool:
    """True for a single path *component* that should never be descended into."""
    return name in _EXCLUDED_DIRS


def in_excluded_dir(path: str) -> bool:
    """True if any component of a repo-relative path is an excluded directory."""
    return any(is_excluded_dir(part) for part in path.split("/")[:-1])


def is_generated_file(path: str) -> bool:
    name = _basename(path).lower()
    if name in _GENERATED_FILENAMES:
        return True
    return name.endswith(_GENERATED_SUFFIXES)


def looks_like_secret_file(path: str) -> bool:
    """True if a path's *name* marks it as a likely secret container.

    Note the ``.env.example`` carve-out: example/template env files are safe and
    are frequently the best documentation of required configuration.
    """
    name = _basename(path).lower()
    if name in {".env.example", ".env.sample", ".env.template"}:
        return False
    if name in _SECRET_FILENAMES:
        return True
    if name.startswith(".env."):
        return True
    return name.endswith(_SECRET_SUFFIXES)


def looks_binary(sample: bytes) -> bool:
    """Heuristic binary check on a leading byte sample.

    A NUL byte is a near-certain binary marker; otherwise we flag content that is
    not valid UTF-8 and carries a high proportion of non-text control bytes.
    """
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        # Non-UTF-8: treat as binary only if it also looks control-byte-heavy,
        # so that legitimately latin-1 text still has a chance downstream.
        text_bytes = bytes(range(0x20, 0x7F)) + b"\t\n\r\f\v\b"
        nontext = sum(1 for b in sample if b not in text_bytes)
        return nontext / len(sample) > 0.30
    return False
