"""负载序列化：把 Pydantic 模型 / dataclass / 自定义对象转换为 JSON 原生结构"""
from dataclasses import asdict, is_dataclass
import json
from typing import Any

from pydantic import BaseModel

def jsonable(payload: Any) -> Any:
    """把任意负载规整为 JSON 原生类型；失败时退回 None（避免阻塞写入）"""
    try:
        return json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        return None

def convert_data(data: Any) -> Any:
    """递归转换数据"""
    if data is None:
        return None
    if hasattr(data, "to_dict"):
        return data.to_dict()
    if hasattr(data, "__dict__"):
        return data.__dict__
    if isinstance(data, list):
        return [convert_data(item) for item in data]
    if isinstance(data, dict):
        return {key: convert_data(value) for key, value in data.items()}
    return data


def serialize_payload(payload: Any) -> Any:
    """
    序列化负载数据为 JSON 原生结构（dict/list/标量）

    Args:
        payload: 需要序列化的负载数据，支持 dict、list、Pydantic 模型、dataclass、带 to_dict/__dict__ 的对象

    Returns:
        JSON 原生结构；无法结构化时原样返回标量

    Raises:
        TypeError: 当 payload 类型不支持时抛出异常
    """
    if isinstance(payload, dict):
        return payload
    elif isinstance(payload, list):
        return [serialize_payload(item) for item in payload]
    elif isinstance(payload, BaseModel):
        return payload.model_dump()
    elif is_dataclass(payload):
        return asdict(payload)  # type: ignore[arg-type]
    else:
        return convert_data(payload)
