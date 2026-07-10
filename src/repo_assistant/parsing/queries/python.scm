; Symbol definitions for Python. @def captures the whole definition node;
; kind and qualified name are derived in the parser from node type and ancestry.
(function_definition) @def
(class_definition) @def

; Imports (raw statements; structured resolution is deferred to Phase 3).
(import_statement) @import
(import_from_statement) @import
