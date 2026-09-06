"""DTO Class 递归解析与重构引擎 + AI Prompt 类型格式化"""
import contextlib
import enum
from typing import Annotated, Any, Callable, Dict, List, Literal, Optional, Type, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo

from .introspection import (
    UNION_TYPES,
    analyze_annotation,
    inspect_class_fields,
    is_container_type,
    is_undefined,
)
from .markers import context
from .resources import acquire_resource


def _unwrap_field_default(raw_default: Any) -> Any:
    """Pydantic 字段默认值在反射层以 FieldInfo 哨兵存储，取其真实 default 以便判定是否可注入"""
    if isinstance(raw_default, FieldInfo):
        return raw_default.default
    return raw_default


# 递归 Class 与 List[Class] 解析与重构引擎
class ClassTypeMeta:
    def __init__(
            self,
            raw_cls: Type,
            kind: str,
            ai_model: Type[BaseModel] | Any,
            ctx_fields: dict,
            res_fields: dict,
            nested_class_fields: dict,
            is_container: bool = False,
            item_meta: "ClassTypeMeta" = None,
            container_origin: Any = None,
    ):
        self.raw_cls = raw_cls
        self.kind = kind
        self.ai_model = ai_model
        self.ctx_fields = ctx_fields
        self.res_fields = res_fields
        self.nested_class_fields = nested_class_fields
        self.is_container = is_container  # 是否为 List/Set 容器
        self.item_meta = item_meta  # 容器内部元素的元数据
        self.container_origin = container_origin


def process_type_meta(tp: Type) -> tuple[Type, ClassTypeMeta | None]:
    """
    【核心递归解析】自顶向下解析：
    支持单个 Class (BaseModel/@dataclass/plain_class) 以及 List[Class] 容器
    """
    # 1. 处理 List[T] / set[T] 容器类型
    is_container, origin, item_type = is_container_type(tp)
    if is_container:
        sub_ai_type, sub_meta = process_type_meta(item_type)
        if sub_meta is not None:
            ai_container_type = origin[sub_ai_type] if origin else List[sub_ai_type]
            container_meta = ClassTypeMeta(
                raw_cls=tp,
                kind="container",
                ai_model=ai_container_type,
                ctx_fields={},
                res_fields={},
                nested_class_fields={},
                is_container=True,
                item_meta=sub_meta,
                container_origin=origin,
            )
            return ai_container_type, container_meta
        else:
            return tp, None

    # 2. 处理单个 Class 类 (BaseModel / @dataclass / plain_class)
    kind, raw_fields = inspect_class_fields(tp)
    if kind == "primitive":
        return tp, None

    ai_fields = {}
    ctx_fields = {}
    res_fields = {}
    nested_class_fields = {}

    for f_name, (f_type, default_val) in raw_fields.items():
        core_type, is_optional, is_context, ctx_key, is_skip, is_resource, resource_name, _ = analyze_annotation(f_type)

        if is_context:
            ctx_fields[f_name] = {"alias": ctx_key, "is_skip": is_skip, "optional": is_optional, "default": default_val}
        elif is_resource:
            res_fields[f_name] = {"resource_name": resource_name or f_name, "optional": is_optional,
                                  "default": default_val}
        else:
            sub_ai_type, sub_meta = process_type_meta(core_type)

            if is_optional and get_origin(f_type) in UNION_TYPES:
                sub_ai_type = Optional[sub_ai_type]

            if sub_meta is not None:
                nested_class_fields[f_name] = sub_meta
                if default_val is ...:
                    try:
                        sub_ai_type()
                        default_val = Field(default_factory=sub_ai_type)
                    except Exception:
                        pass

            ai_fields[f_name] = (sub_ai_type, default_val)

    proxy_model = create_model(
        f"{tp.__name__}_AIModel",
        __config__=ConfigDict(arbitrary_types_allowed=True),
        **ai_fields,
    )

    meta = ClassTypeMeta(
        raw_cls=tp,
        kind=kind,
        ai_model=proxy_model,
        ctx_fields=ctx_fields,
        res_fields=res_fields,
        nested_class_fields=nested_class_fields,
    )
    return proxy_model, meta


async def reconstruct_instance(
        meta: ClassTypeMeta, ai_val: Any, resources: Dict[str, Callable], exit_stack: contextlib.AsyncExitStack
) -> Any:
    """
    【核心递归重构】自底向上恢复对象实例：
    支持 List[BaseModel], List[Dataclass], List[PlainClass] 数组元素的逐个注入与实例化
    """
    if ai_val is None:
        return None

    # 1. 容器逻辑：遍历数组递归重构每一个元素
    if meta.is_container:
        if not isinstance(ai_val, (list, tuple, set)):
            return ai_val
        reconstructed_items = [
            await reconstruct_instance(meta.item_meta, item, resources, exit_stack)
            for item in ai_val
        ]
        if meta.container_origin is set:
            return set(reconstructed_items)
        return reconstructed_items

    # 2. 单个 Class 实例重构逻辑
    data_dict = ai_val.model_dump() if isinstance(ai_val, BaseModel) else dict(ai_val)
    cxt = context.get()

    # 注入 Context
    for f_name, ctx_info in meta.ctx_fields.items():
        if ctx_info["is_skip"]:
            continue
        key = ctx_info["alias"] or f_name
        if key in cxt:
            data_dict[f_name] = cxt[key]
        else:
            default = _unwrap_field_default(ctx_info.get("default"))
            if default is not ... and not is_undefined(default):
                data_dict[f_name] = default
            elif not ctx_info["optional"]:
                raise KeyError(f"Context 中缺少 key: '{key}' (用于 '{meta.raw_cls.__name__}.{f_name}')")

    # 注入 Resource
    for f_name, res_info in meta.res_fields.items():
        r_name = res_info["resource_name"]
        if r_name in resources:
            getter = resources[r_name]
            data_dict[f_name] = await acquire_resource(getter, exit_stack)
        else:
            default = _unwrap_field_default(res_info.get("default"))
            if default is not ... and not is_undefined(default):
                data_dict[f_name] = default
            elif not res_info["optional"]:
                raise ValueError(f"Resource '{r_name}' 未注册 (用于 '{meta.raw_cls.__name__}.{f_name}')")

    # 递归重构子属性 (子 Class 或 List[子 Class])
    for f_name, child_meta in meta.nested_class_fields.items():
        child_ai_val = getattr(ai_val, f_name, None) if isinstance(ai_val, BaseModel) else data_dict.get(f_name)
        data_dict[f_name] = await reconstruct_instance(child_meta, child_ai_val, resources, exit_stack)

    # 根据 Class 种类实例化
    if meta.kind == "basemodel":
        return meta.raw_cls.model_validate(data_dict)
    else:  # @dataclass 或 plain_class
        return meta.raw_cls(**data_dict)


def format_type_for_prompt(tp: Any) -> str:
    """递归格式化工具树 Prompt（支持 List[Class] 展开描述）"""
    origin = get_origin(tp)

    if origin is Annotated:
        return format_type_for_prompt(get_args(tp)[0])

    # 支持 Literal['PAID', 'UNPAID'] 展现给 AI
    if origin is Literal:
        literal_vals = "/".join([repr(v) for v in get_args(tp)])
        return f"Literal[{literal_vals}]"

    # 支持 Enum 枚举展现给 AI
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        enum_vals = "/".join([str(e.value) for e in tp])
        return f"Enum[{enum_vals}]"

    if origin in UNION_TYPES:
        args = get_args(tp)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) < len(args):
            return f"{'/'.join([format_type_for_prompt(a) for a in non_none])}?"
        return "/".join([format_type_for_prompt(a) for a in args])

    is_container, container_origin, item_type = is_container_type(tp)
    if is_container:
        formatted_item = format_type_for_prompt(item_type)
        return f"List[{formatted_item}]"

    ai_type, meta = process_type_meta(tp)
    if meta is not None:
        nested_fields = []
        for f_name, f_info in ai_type.model_fields.items():
            f_type_str = format_type_for_prompt(f_info.annotation)
            opt_mark = "?" if not f_info.is_required() else ""
            desc = f" [{f_info.description}]" if f_info.description else ""
            nested_fields.append(f"{f_name}: {f_type_str}{opt_mark}{desc}")
        return f"{meta.raw_cls.__name__}{{{', '.join(nested_fields)}}}"

    if hasattr(tp, "__name__"):
        return tp.__name__

    return str(tp).replace("typing.", "")
