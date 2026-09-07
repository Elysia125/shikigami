"""AbstractAgentTrace：Agent 工具调用追踪的后端抽象接口。

全局约定（后端实现方与调用方共享）：
- timestamp 统一为 Unix 秒（int），由后端在 record 写入时生成。
- status 约定取值 success / error（UniversalToolRegistry 埋点写入）。
- extras 为自由扩展字典；record 未提供时按空字典处理。
- 分页参数 page 从 1 开始；无数据时返回 total=0、items=[] 的 PageResult。
- 数据模型分散在同级 models.py，分页/序列化工具在 shikigami.utils 子包；
  内置数据库实现见同级 default_trace.py 的 SqlAlchemyTrace（SQLite/MySQL/PostgreSQL 等）。
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from .models import TraceRecord, TraceSummary
from ..utils.pagination import PageResult


class AbstractAgentTrace(ABC):
    """Agent 工具追踪后端抽象接口。

    UniversalToolRegistry 的埋点行为（实现方可据此对齐语义）：
    - 业务工具经 aexecute 执行完毕后调用 record(...)，status 写 success / error；
    - 埋点所需 agent_id/trace_id 从 context 中读取 agentId/traceId（无则回退 agent_id/trace_id），
      缺任一即不记录；元工具（list_categories/list_tools/get_tool_info/execute_tool 等）不记录。
    """

    @abstractmethod
    async def record(self, agent_id: str, trace_id: str, tool_name: str, args: dict, result: Any,
                     status: str = "success", extras: Optional[dict] = None) -> None:
        """
        记录一次工具调用。

        :param agent_id: Agent 的 ID。
        :param trace_id: 本次会话/回合的 ID。
        :param tool_name: 被调用的工具名。
        :param args: 工具入参（JSON 原生结构）。
        :param result: 工具返回结果（JSON 原生结构，失败时为错误文本）。
        :param status: 执行状态，约定 success / error。
        :param extras: 扩展信息（自由字典）。
        """
        pass

    @abstractmethod
    async def get_trace(self, trace_id: str, page: int = 1, size: int = 10) -> PageResult[TraceRecord]:
        """
        按时间升序分页查询某次会话的全部工具调用明细。

        :param trace_id: 会话/回合 ID。
        :param page: 页码，从 1 开始。
        :param size: 每页条数。
        :return: 分页的工具调用记录列表。
        """
        pass

    @abstractmethod
    async def get_traces(self, agent_id: str, page: int = 1, page_size: int = 10) -> PageResult[TraceSummary]:
        """
        按最近活动倒序分页查询某 Agent 的会话摘要列表。

        :param agent_id: Agent 的 ID。
        :param page: 页码，从 1 开始。
        :param page_size: 每页条数。
        :return: 分页的会话摘要列表（TraceSummary）。
        """
        pass

    @abstractmethod
    async def get_stats(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """
        统计信息。agent_id 为空时统计全部数据。

        :param agent_id: 可选，仅统计该 Agent。
        :return: {"agent_id": agent_id, "trace_count": 会话数, "call_count": 工具调用总数, "tool_count": 去重工具数}
        """
        pass

    @abstractmethod
    async def delete_trace(self, trace_id: str) -> bool:
        """
        删除某次会话的全部记录（无论属于哪个 Agent）。

        :param trace_id: 会话/回合 ID。
        :return: 是否确有数据被删除。
        """
        pass

    @abstractmethod
    async def delete_agent_traces(self, agent_id: str) -> int:
        """
        删除某 Agent 的全部会话记录。

        :param agent_id: Agent 的 ID。
        :return: 删除的记录条数。
        """
        pass

    @abstractmethod
    async def delete_before(self, timestamp: int) -> int:
        """
        按时间批量删除（保留策略用）：删除所有记录时间早于 timestamp 的记录。

        :param timestamp: Unix 秒时间戳（不含该时刻本身）。
        :return: 删除的记录条数。
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """释放后端占用的资源（连接池等）；应用关闭时调用"""
        pass
