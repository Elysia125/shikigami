"""兼容转发层：原单文件实现已拆分至 shikigami/registry/ 子包，此处保持旧导入路径可用。

   例如 from shikigami.register import parse_docstring / ContextParam / UniversalToolRegistry
   仍指向拆分后的同一实现与同一默认单例。
"""
from .registry import (  # noqa: F401
    UNION_TYPES,
    ClassTypeMeta,
    ContextParam,
    EmptySchema,
    ResourceParam,
    SignatureSchemaBuilder,
    SignatureSchemaResult,
    ToolExecuteSchema,
    ToolInfoSchema,
    ToolsSchema,
    UniversalToolRegistry,
    acquire_resource,
    analyze_annotation,
    context,
    default_registry,
    format_type_for_prompt,
    get_actual_default,
    inspect_class_fields,
    is_container_type,
    is_undefined,
    parse_docstring,
    process_type_meta,
    reconstruct_instance,
    set_agent_context,
)

# 旧别名：保持 from shikigami.register import registry 可用
registry = default_registry
