"""类型注解 / 反射分析层（纯函数，无注册状态）"""
import dataclasses
import inspect
import types
from dataclasses import is_dataclass
from typing import Any, Iterable, List, Sequence, Type, Union, get_args, get_origin, get_type_hints, Annotated

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from .markers import ContextParam, ResourceParam

UNION_TYPES = (Union, types.UnionType) if hasattr(types, "UnionType") else (Union,)


def inspect_class_fields(cls: Type) -> tuple[str, dict[str, tuple[Type, Any]]]:
    """统一提取 Class 的类型和字段信息"""
    if not isinstance(cls, type):
        return "primitive", {}

    # 1. Pydantic BaseModel
    if issubclass(cls, BaseModel):
        hints = get_type_hints(cls, include_extras=True)
        fields = {}
        for f_name, f_info in cls.model_fields.items():
            raw_annotation = hints.get(f_name, f_info.annotation)
            fields[f_name] = (raw_annotation, f_info)
        return "basemodel", fields

    # 2. Python @dataclass
    if is_dataclass(cls):
        hints = get_type_hints(cls, include_extras=True)
        fields = {}
        for f in dataclasses.fields(cls):
            raw_annotation = hints.get(f.name, f.type)
            if f.default != dataclasses.MISSING:
                default_val = f.default
            elif f.default_factory != dataclasses.MISSING:
                default_val = f.default_factory()
            else:
                default_val = ...
            fields[f.name] = (raw_annotation, default_val)
        return "dataclass", fields

    # 3. 普通 Class (通过反射 __init__ 方法)
    init_method = getattr(cls, "__init__", None)
    if init_method and init_method != object.__init__:
        sig = inspect.signature(init_method)
        hints = get_type_hints(init_method, include_extras=True)
        fields = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            hint = hints.get(param_name, param.annotation)
            default_val = ... if param.default == inspect.Parameter.empty else param.default
            fields[param_name] = (hint, default_val)
        return "plain_class", fields

    return "primitive", {}


def is_container_type(tp: Any) -> tuple[bool, Any, Any]:
    """检查类型是否为 List[T], list[T], set[T] 等泛型容器"""
    origin = get_origin(tp)
    if origin in (list, set, tuple, List, Sequence, Iterable):
        args = get_args(tp)
        if args:
            return True, origin, args[0]
    return False, None, tp


def analyze_annotation(annotation: Any, field_info: Any = None, doc_description: str = None):
    """解析类型注解：兼顾 Annotated、FieldInfo 和 Docstring"""
    is_context = False
    context_key = None
    is_skip = False
    is_resource = False
    resource_name = None
    is_optional = False
    description = doc_description
    core_type = annotation

    metadata_list = []

    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        core_type = args[0]
        metadata_list.extend(args[1:])

    if isinstance(field_info, FieldInfo):
        if field_info.description:
            description = field_info.description
        if field_info.metadata:
            metadata_list.extend(field_info.metadata)

    for m in metadata_list:
        if isinstance(m, ContextParam):
            is_context = True
            context_key = m.alias
            is_skip = m.skip
        elif m is ContextParam or (isinstance(m, type) and issubclass(m, ContextParam)):
            is_context = True
        elif isinstance(m, ResourceParam):
            is_resource = True
            resource_name = m.resource_name
        elif m is ResourceParam or (isinstance(m, type) and issubclass(m, ResourceParam)):
            is_resource = True
        elif isinstance(m, str) and not field_info:
            description = m

    origin = get_origin(core_type)
    if origin in UNION_TYPES:
        union_args = get_args(core_type)
        if type(None) in union_args:
            is_optional = True
            non_none = [a for a in union_args if a is not type(None)]
            core_type = non_none[0] if len(non_none) == 1 else Union[tuple(non_none)]

    return core_type, is_optional, is_context, context_key, is_skip, is_resource, resource_name, description


def is_undefined(val: Any) -> bool:
    return val is ... or str(type(val)) == "<class 'pydantic_core._pydantic_core.PydanticUndefinedType'>"


def get_actual_default(param: inspect.Parameter, type_hint: Any = None) -> Any:
    default_val = param.default
    if default_val != inspect.Parameter.empty:
        if isinstance(default_val, FieldInfo):
            if not is_undefined(default_val.default):
                return default_val.default
        else:
            return default_val

    if type_hint and get_origin(type_hint) is Annotated:
        for metadata in get_args(type_hint)[1:]:
            if isinstance(metadata, FieldInfo):
                if not is_undefined(metadata.default):
                    return metadata.default

    return inspect.Parameter.empty
