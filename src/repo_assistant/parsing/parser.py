"""tree-sitter parsing and symbol extraction.

Each supported language maps to a tree-sitter grammar plus a ``.scm`` query file
that captures definition and import nodes. Kind, qualified name, and parent are
derived from node type and ancestry so a single small query per language suffices
(docs/adr/0002-parsing-and-chunking.md).
"""

from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from tree_sitter import Node, Query, QueryCursor
from tree_sitter_language_pack import get_language, get_parser

from repo_assistant.core.errors import ParsingError
from repo_assistant.parsing.models import Import, ParsedFile, Symbol, SymbolKind


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    grammar: str  # name understood by tree_sitter_language_pack
    query_file: str
    def_kinds: dict[str, SymbolKind]


# function_definition is resolved to FUNCTION vs METHOD by context (see below).
_PYTHON = LanguageSpec(
    grammar="python",
    query_file="python.scm",
    def_kinds={"function_definition": SymbolKind.FUNCTION, "class_definition": SymbolKind.CLASS},
)
_TS_KINDS = {
    "function_declaration": SymbolKind.FUNCTION,
    "class_declaration": SymbolKind.CLASS,
    "method_definition": SymbolKind.METHOD,
    "interface_declaration": SymbolKind.INTERFACE,
    "type_alias_declaration": SymbolKind.TYPE,
    "enum_declaration": SymbolKind.ENUM,
}
_JS_KINDS = {
    "function_declaration": SymbolKind.FUNCTION,
    "class_declaration": SymbolKind.CLASS,
    "method_definition": SymbolKind.METHOD,
}

_SPECS: dict[str, LanguageSpec] = {
    "python": _PYTHON,
    "typescript": LanguageSpec("typescript", "typescript.scm", _TS_KINDS),
    "tsx": LanguageSpec("tsx", "typescript.scm", _TS_KINDS),
    "javascript": LanguageSpec("javascript", "javascript.scm", _JS_KINDS),
}


def supported_languages() -> frozenset[str]:
    return frozenset(_SPECS)


@cache
def _load_query(language: str) -> Query:
    spec = _SPECS[language]
    source = (files("repo_assistant.parsing.queries") / spec.query_file).read_text("utf-8")
    return Query(get_language(spec.grammar), source)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _signature(node: Node, source: bytes) -> str:
    """First physical line of a definition — its declaration/signature."""
    text = _node_text(node, source)
    return text.split("\n", 1)[0].strip()


def _python_docstring(node: Node, source: bytes) -> str | None:
    body = node.child_by_field_name("body")
    if body is None or body.named_child_count == 0:
        return None
    first = body.named_children[0]
    # Depending on grammar version the leading string may be wrapped in an
    # expression_statement or sit directly in the block.
    if first.type == "expression_statement" and first.named_child_count > 0:
        first = first.named_children[0]
    if first.type != "string":
        return None
    contents = [c for c in first.named_children if c.type == "string_content"]
    if contents:
        text = "".join(_node_text(c, source) for c in contents).strip()
    else:
        text = _node_text(first, source).strip().strip("\"'").strip()
    return text or None


def _enclosing_defs(node: Node, def_types: frozenset[str]) -> list[Node]:
    """Ancestor definition nodes, outermost first (excluding ``node`` itself)."""
    chain: list[Node] = []
    parent = node.parent
    while parent is not None:
        if parent.type in def_types:
            chain.append(parent)
        parent = parent.parent
    chain.reverse()
    return chain


def _name_of(node: Node, source: bytes) -> str | None:
    name_node = node.child_by_field_name("name")
    return _node_text(name_node, source) if name_node is not None else None


def parse_file(path: str, language: str, source: bytes) -> ParsedFile:
    """Parse ``source`` and extract symbols and imports."""
    if language not in _SPECS:
        raise ParsingError(f"Unsupported language for parsing: {language!r}")

    spec = _SPECS[language]
    tree = get_parser(spec.grammar).parse(source)
    parsed = ParsedFile(path=path, language=language, source=source, root=tree.root_node)

    captures = QueryCursor(_load_query(language)).captures(tree.root_node)
    def_types = frozenset(spec.def_kinds)

    for def_node in captures.get("def", []):
        name = _name_of(def_node, source)
        if name is None:
            continue  # anonymous/unsupported definition form; skip in Phase 1

        ancestors = _enclosing_defs(def_node, def_types)
        kind = spec.def_kinds[def_node.type]
        # A Python function nested directly inside a class is a method.
        if (
            def_node.type == "function_definition"
            and ancestors
            and ancestors[-1].type == "class_definition"
        ):
            kind = SymbolKind.METHOD

        name_path = [n for a in ancestors if (n := _name_of(a, source)) is not None]
        qualified_name = ".".join([*name_path, name])
        parent = ".".join(name_path) if name_path else None
        docstring = _python_docstring(def_node, source) if language == "python" else None

        parsed.symbols.append(
            Symbol(
                name=name,
                qualified_name=qualified_name,
                kind=kind,
                start_line=def_node.start_point[0] + 1,
                end_line=def_node.end_point[0] + 1,
                start_byte=def_node.start_byte,
                end_byte=def_node.end_byte,
                signature=_signature(def_node, source),
                docstring=docstring,
                parent=parent,
            )
        )

    for import_node in captures.get("import", []):
        parsed.imports.append(
            Import(
                text=_node_text(import_node, source),
                start_line=import_node.start_point[0] + 1,
                end_line=import_node.end_point[0] + 1,
            )
        )

    parsed.symbols.sort(key=lambda s: (s.start_byte, s.end_byte))
    return parsed
