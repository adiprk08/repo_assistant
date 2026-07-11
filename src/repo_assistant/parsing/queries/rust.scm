; Symbol definitions for Rust. @def captures the whole definition node; kind and
; qualified name are derived in the parser from node type and ancestry. Methods
; live in `impl` blocks (unnamed) — the parser prefixes them with the impl target
; type, and function_signature_item captures trait method declarations.
(function_item) @def
(function_signature_item) @def
(struct_item) @def
(enum_item) @def
(union_item) @def
(trait_item) @def
(type_item) @def
(mod_item) @def

; Imports.
(use_declaration) @import
