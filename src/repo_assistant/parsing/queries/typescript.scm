; Symbol definitions for TypeScript/TSX (also used for JavaScript, which is a
; subset — unmatched node types simply produce no captures).
(function_declaration) @def
(class_declaration) @def
(method_definition) @def
(interface_declaration) @def
(type_alias_declaration) @def
(enum_declaration) @def

; Exported forms wrap the declaration; the inner node is still captured above.

; Imports.
(import_statement) @import
