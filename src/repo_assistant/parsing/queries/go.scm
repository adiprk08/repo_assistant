; Symbol definitions for Go. @def captures the whole definition node; kind and
; qualified name are derived in the parser from node type and ancestry. type_spec
; (not type_declaration) is captured so grouped `type ( A ...; B ... )` blocks
; yield one symbol each; struct vs interface is resolved from its child node.
(function_declaration) @def
(method_declaration) @def
(type_spec) @def

; Imports (raw statements; structured resolution is deferred).
(import_declaration) @import
