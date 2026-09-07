"""Trace 数据模型：单条工具调用记录与会话聚合摘要"""
from dataclasses import dataclass
from typing import Any


@dataclass
class TraceRecord:
    """单次工具调用的执行记录（一次 trace 会话内的一次调用）"""
    agent_id: str
    trace_id: str
    tool_name: str
    args: dict
    result: Any
    timestamp: int  # Unix timestamp（秒），由后端写入时生成
    extras: dict = None  # type: ignore[assignment]
    status: str = "success"  # 执行状态，约定取值 success / error（registry 埋点写入）


@dataclass
class TraceSummary:
    """一个 trace（一次 Agent 会话/回合）的聚合摘要，get_traces 的元素"""
    agent_id: str
    trace_id: str
    tool_count: int  # 该会话内的工具调用次数
    start_time: int  # 首次调用时间（Unix 秒）
    end_time: int  # 最后一次调用时间（Unix 秒）
