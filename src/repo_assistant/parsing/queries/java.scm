; Symbol definitions for Java. @def captures the whole definition node; kind and
; qualified name are derived in the parser from node type and ancestry (methods
; nest inside a class/interface/enum body, so their qualified name is prefixed).
(class_declaration) @def
(interface_declaration) @def
(enum_declaration) @def
(record_declaration) @def
(method_declaration) @def
(constructor_declaration) @def

; Imports.
(import_declaration) @import
