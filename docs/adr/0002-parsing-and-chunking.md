# ADR-0002: tree-sitter parsing and AST-aware chunking

**Status:** Accepted (2026-07-07)

## Context

Chunking is the highest-leverage decision in code RAG. Fixed-size windows split functions mid-body and glue unrelated symbols together, poisoning both embeddings and citations. We also need symbol extraction (functions, classes, imports, spans) for the symbol index and code graph, across many languages, on possibly syntactically-broken code.

## Decision

- **Parsing:** tree-sitter via `tree-sitter-language-pack` (prebuilt grammars, error-tolerant, fast, incremental). Per-language query files extract symbols, signatures, docstrings, imports/exports.
- **Chunking (code):** a chunk is a union of *complete* AST nodes within a ~1,200-token budget — greedy merge of small adjacent siblings, recursive split of oversized nodes at statement boundaries (the cAST approach, which shows consistent retrieval and downstream gains over fixed-size splitting in published evaluations). Every chunk gets a breadcrumb header (`path › enclosing class › signature`) embedded with the body but excluded from the citation span.
- **Chunking (non-code):** heading-aware splitter for Markdown/rST; key-path-aware for JSON/YAML/TOML.
- **Fallback:** line-window chunker with overlap for languages without a grammar — searchable, no symbols.
- **Language tiers:** T1 Python, TypeScript/JavaScript (Phase 1); T2 Go, Java, Rust (Phase 3); T3 C/C++, C#, Ruby, PHP (Phase 6).

## Alternatives considered

- **Fixed-size token windows** — baseline; known-worse for code, kept only as the fallback.
- **Language-native parsers** (ast, ts-morph, go/ast…) — higher fidelity per language, O(languages) integration and maintenance cost; rejected.
- **LSP/SCIP indexers** — compiler-grade symbols, but require per-language toolchain setup and often a build; wrong cost profile for arbitrary user repos. Recorded as the precision upgrade path in ADR-0005.

## Consequences

- Uniform multi-language pipeline; adding a language = grammar + query file.
- Symbol resolution is syntactic, not semantic — accepted, with precision enforced downstream (ADR-0005, ADR-0006).
- Chunk-budget and header format are eval-tunable parameters, exercised by the Phase 2 harness ablations.
