"""Shikigami Registry 子包（由原单文件 shikigami/register.py 拆分）

模块职责：
- markers        注入标记与请求上下文（ContextParam / ResourceParam / context / set_agent_context）
- docstring      Docstring 解析（Sphinx / Google / NumPy 风格）
- introspection  类型注解与反射分析（analyze_annotation / inspect_class_fields / ...）
- resources      资源生命周期引擎（acquire_resource）
- class_meta     DTO Class 递归解析与重构引擎（ClassTypeMeta / process_type_meta / reconstruct_instance / format_type_for_prompt）
- schemas        固定元工具（list_categories 等）的 Pydantic Schema
- schema_builder 函数签名 → AI 输入 Schema 编译器（register 与 meta_tool 共用）
- registry       UniversalToolRegistry 主类与默认单例
"""
from .class_meta import (
    ClassTypeMeta,
    format_type_for_prompt,
    process_type_meta,
    reconstruct_instance,
)
from .docstring import parse_docstring
from .introspection import (
    UNION_TYPES,
    analyze_annotation,
    get_actual_default,
    inspect_class_fields,
    is_container_type,
    is_undefined,
)
from .markers import ContextParam, ResourceParam, context, set_agent_context
from .registry import UniversalToolRegistry, default_registry
from .resources import acquire_resource
from .schema_builder import SignatureSchemaBuilder, SignatureSchemaResult
from .schemas import EmptySchema, ToolExecuteSchema, ToolInfoSchema, ToolsSchema

__all__ = [
    "UNION_TYPES",
    "analyze_annotation",
    "acquire_resource",
    "inspect_class_fields",
    "is_container_type",
    "is_undefined",
    "get_actual_default",
    "parse_docstring",
    "context",
    "ContextParam",
    "ResourceParam",
    "set_agent_context",
    "ClassTypeMeta",
    "process_type_meta",
    "reconstruct_instance",
    "format_type_for_prompt",
    "EmptySchema",
    "ToolsSchema",
    "ToolInfoSchema",
    "ToolExecuteSchema",
    "SignatureSchemaBuilder",
    "SignatureSchemaResult",
    "UniversalToolRegistry",
    "default_registry",
]
