"""函数签名 → AI 输入 Schema 编译器（register() 与 meta_tool() 共用的参数解析逻辑）"""
import dataclasses
import inspect
from typing import Any, Callable, Dict, Type, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo

from .class_meta import process_type_meta
from .docstring import parse_docstring
from .introspection import analyze_annotation, get_actual_default


@dataclasses.dataclass
class SignatureSchemaResult:
    """一次签名编译产物：AI 可见 Schema + Context/Resource 注入映射 + 嵌套 Class 元数据"""
    schema: Type[BaseModel]
    context_params_map: Dict[str, dict]
    resource_params_map: Dict[str, dict]
    class_info_map: Dict[str, Any]
    summary: str | None = None


class SignatureSchemaBuilder:
    """【签名 → Schema 编译器】
    把函数签名（Annotated / FieldInfo / Docstring / Class DTO）编译为 AI 可见的
    Pydantic 输入模型，并识别 ContextParam / ResourceParam 注入参数。
    register() 与 meta_tool() 共用；子类可覆写 build() 自定义参数解析规则。
    """

    def build(self, func: Callable, name: str, *, allow_resource: bool = True) -> SignatureSchemaResult:
        doc_summary, doc_param_docs = parse_docstring(func)
        sig = inspect.signature(func)
        hints = get_type_hints(func, include_extras=True)

        pydantic_fields = {}
        context_params_map = {}
        resource_params_map = {}
        class_info_map = {}

        for param_name, param in sig.parameters.items():
            hint = hints.get(param_name, param.annotation)
            doc_p_desc = doc_param_docs.get(param_name)

            core_type, is_optional, is_context, ctx_key, is_skip, is_resource, resource_name, p_desc = analyze_annotation(
                hint, field_info=param.default if isinstance(param.default, FieldInfo) else None,
                doc_description=doc_p_desc
            )
            if is_context:
                context_params_map[param_name] = {
                    "alias": ctx_key,
                    "is_skip": is_skip,
                    "optional": is_optional,
                    "default": get_actual_default(param, hint),
                }
            elif is_resource:
                if not allow_resource:
                    raise ValueError(f"Resource parameter '{param_name}' is not supported in meta tools.")
                resource_params_map[param_name] = {
                    "resource_name": resource_name or param_name,
                    "optional": is_optional,
                    "default": get_actual_default(param, hint),
                }
            else:
                ai_type, meta = process_type_meta(hint)

                if isinstance(param.default, FieldInfo):
                    target_field = param.default
                    if p_desc and not target_field.description:
                        target_field.description = p_desc
                else:
                    actual_default = (
                        None
                        if (param.default == inspect.Parameter.empty and is_optional)
                        else (param.default if param.default != inspect.Parameter.empty else ...)
                    )
                    target_field = Field(default=actual_default,
                                         description=p_desc) if p_desc else actual_default

                if meta is not None:
                    class_info_map[param_name] = meta
                    pydantic_fields[param_name] = (ai_type, target_field)
                else:
                    pydantic_fields[param_name] = (hint, target_field)

        return SignatureSchemaResult(
            schema=create_model(
                f"{name}_InputSchema", __config__=ConfigDict(arbitrary_types_allowed=True), **pydantic_fields
            ),
            context_params_map=context_params_map,
            resource_params_map=resource_params_map,
            class_info_map=class_info_map,
            summary=doc_summary,
        )
