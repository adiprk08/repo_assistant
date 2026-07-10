"""Symbol and import extraction across supported languages."""

from repo_assistant.parsing import parse_file
from repo_assistant.parsing.models import SymbolKind


def _by_name(parsed, name):
    return next(s for s in parsed.symbols if s.qualified_name == name)


def test_python_functions_classes_methods_and_docstrings() -> None:
    src = (
        b"import os\n"
        b"from typing import List\n\n"
        b"def top(x):\n"
        b'    """Top doc."""\n'
        b"    return x\n\n"
        b"class Service:\n"
        b"    def refresh(self, token):\n"
        b'        """Refresh doc."""\n'
        b"        return token\n"
    )
    parsed = parse_file("svc.py", "python", src)

    top = _by_name(parsed, "top")
    assert top.kind is SymbolKind.FUNCTION
    assert top.parent is None
    assert top.docstring == "Top doc."
    assert top.signature == "def top(x):"

    refresh = _by_name(parsed, "Service.refresh")
    assert refresh.kind is SymbolKind.METHOD
    assert refresh.parent == "Service"
    assert refresh.docstring == "Refresh doc."

    assert _by_name(parsed, "Service").kind is SymbolKind.CLASS
    assert [i.text for i in parsed.imports] == ["import os", "from typing import List"]


def test_python_line_spans_are_one_indexed_inclusive() -> None:
    src = b"def a():\n    return 1\n"
    parsed = parse_file("a.py", "python", src)
    a = _by_name(parsed, "a")
    assert (a.start_line, a.end_line) == (1, 2)


def test_typescript_interface_class_method_type() -> None:
    src = (
        b"export interface User { id: string; }\n"
        b"export type Id = string;\n"
        b"export class Repo {\n"
        b"  async find(id: string): Promise<User> { return { id }; }\n"
        b"}\n"
        b"function helper() { return 1; }\n"
    )
    parsed = parse_file("repo.ts", "typescript", src)
    kinds = {s.qualified_name: s.kind for s in parsed.symbols}
    assert kinds["User"] is SymbolKind.INTERFACE
    assert kinds["Id"] is SymbolKind.TYPE
    assert kinds["Repo"] is SymbolKind.CLASS
    assert kinds["Repo.find"] is SymbolKind.METHOD
    assert kinds["helper"] is SymbolKind.FUNCTION


def test_javascript_class_and_function() -> None:
    src = b"class Widget {\n  render() { return null; }\n}\nfunction mount() {}\n"
    parsed = parse_file("w.js", "javascript", src)
    kinds = {s.qualified_name: s.kind for s in parsed.symbols}
    assert kinds["Widget"] is SymbolKind.CLASS
    assert kinds["Widget.render"] is SymbolKind.METHOD
    assert kinds["mount"] is SymbolKind.FUNCTION


def test_syntactically_broken_code_does_not_crash() -> None:
    # tree-sitter is error-tolerant; extraction should degrade, not raise.
    parsed = parse_file("broken.py", "python", b"def oops(:\n    return\nclass \n")
    assert isinstance(parsed.symbols, list)
