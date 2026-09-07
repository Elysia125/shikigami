import time
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from shikigami import UniversalToolRegistry, context
from shikigami.trace import SqlAlchemyTrace, TraceRecord, TraceSummary
from shikigami.utils.pagination import PageResult


@pytest.fixture
async def backend(tmp_path):
    b = SqlAlchemyTrace(url=f"sqlite+aiosqlite:///{tmp_path}/trace_test.db")
    yield b
    await b.close()


@pytest.fixture
def reg():
    r = UniversalToolRegistry()
    r.add_category("订单", "订单管理模块")
    return r


def _set_agent_context(agent_id: str = None, trace_id: str = None):
    """设置 registry 埋点需要的上下文并返回还原函数"""
    ctx = {"scopes": ("global",)}
    if agent_id:
        ctx["agentId"] = agent_id
    if trace_id:
        ctx["traceId"] = trace_id
    token = context.set(ctx)
    return lambda: context.reset(token)


# ==========================================
# 1. SqlAlchemyTrace 数据读写
# ==========================================

class TestSqlAlchemyBackendCRUD:

    async def test_record_and_get_trace_paged(self, backend):
        """record 后可按插入顺序分页查回明细，args/result/extras/status JSON 无损往返"""
        await backend.record("agent-1", "trace-1", "tool_b", {"x": 1}, {"ok": True})
        await backend.record("agent-1", "trace-1", "tool_a", {"x": 2}, "plain string")
        await backend.record("agent-1", "trace-1", "tool_c", {"x": 3}, None,
                             status="error", extras={"note": "boom"})

        page1 = await backend.get_trace("trace-1", page=1, size=2)
        assert isinstance(page1, PageResult)
        assert page1.total == 3
        assert page1.total_page == 2
        assert [r.tool_name for r in page1.items] == ["tool_b", "tool_a"]

        page2 = await backend.get_trace("trace-1", page=2, size=2)
        assert [r.tool_name for r in page2.items] == ["tool_c"]

        first = page1.items[0]
        assert isinstance(first, TraceRecord)
        assert first.agent_id == "agent-1"
        assert first.args == {"x": 1}
        assert first.result == {"ok": True}
        assert first.extras == {}
        assert first.status == "success"  # status 缺省为 success
        assert first.timestamp > 0

        # result 为字符串/None 的场景同样可查回
        assert page1.items[1].result == "plain string"
        assert page2.items[0].result is None
        assert page2.items[0].status == "error"
        assert page2.items[0].extras == {"note": "boom"}

    async def test_get_traces_grouped_summaries_and_stats(self, backend):
        """get_traces 按会话聚合出摘要；get_stats 支持全局与单 Agent 统计"""
        # agent-1: trace-t1 两次调用、trace-t2 一次调用
        await backend.record("agent-1", "trace-t1", "find_order", {}, {})
        await backend.record("agent-1", "trace-t1", "pay_order", {}, {})
        await backend.record("agent-1", "trace-t2", "find_order", {}, {})
        # agent-2: 一次调用
        await backend.record("agent-2", "trace-t3", "send_msg", {}, {})

        result = await backend.get_traces("agent-1")
        assert result.total == 2
        by_trace = {s.trace_id: s for s in result.items}
        assert all(isinstance(s, TraceSummary) for s in result.items)
        assert by_trace["trace-t1"].tool_count == 2
        assert by_trace["trace-t2"].tool_count == 1
        for s in by_trace.values():
            assert s.agent_id == "agent-1"
            assert 0 < s.start_time <= s.end_time

        # 分页
        paged = await backend.get_traces("agent-1", page=2, page_size=1)
        assert paged.total == 2
        assert len(paged.items) == 1

        stats_a1 = await backend.get_stats("agent-1")
        assert stats_a1 == {"agent_id": "agent-1", "trace_count": 2,
                            "call_count": 3, "tool_count": 2}
        stats_all = await backend.get_stats()
        assert stats_all["agent_id"] is None
        assert stats_all["trace_count"] == 3
        assert stats_all["call_count"] == 4
        assert stats_all["tool_count"] == 3

    async def test_delete_family(self, backend):
        """delete_trace / delete_agent_traces / delete_before 删除并返回影响行数"""
        await backend.record("agent-1", "trace-t1", "tool_a", {}, {})
        await backend.record("agent-1", "trace-t1", "tool_b", {}, {})
        await backend.record("agent-1", "trace-t2", "tool_a", {}, {})

        # 删除不存在的 trace 返回 False
        assert await backend.delete_trace("not-exists") is False
        # 删除 trace-t1（两条记录）
        assert await backend.delete_trace("trace-t1") is True
        assert (await backend.get_trace("trace-t1")).total == 0
        # 删除整个 agent
        assert await backend.delete_agent_traces("agent-1") == 1
        assert (await backend.get_traces("agent-1")).total == 0

        # delete_before：早于时刻的记录全部删除
        await backend.record("agent-1", "trace-new", "tool_c", {}, {})
        assert await backend.delete_before(int(time.time()) + 60) == 1
        assert (await backend.get_stats("agent-1"))["call_count"] == 0

    async def test_constructor_validation(self, tmp_path):
        """url 与 engine 必须二选一；同步驱动 URL 直接拒绝"""
        with pytest.raises(ValueError):
            SqlAlchemyTrace()
        with pytest.raises(ValueError):
            SqlAlchemyTrace(url="sqlite:///plain_sync.db")
        with pytest.raises(ValueError):
            SqlAlchemyTrace(url="mysql+pymysql://u:p@localhost/db")

    async def test_record_with_model_payload_not_crash(self, backend):
        """直接调用 record 传入非 JSON 原生对象（Pydantic 模型）时兜底存储、不抛错"""

        class Model(BaseModel):
            order_id: str

        await backend.record("agent-1", "trace-m1", "create_order", {"m": Model(order_id="A")},
                             result=Model(order_id="A"))
        recs = await backend.get_trace("trace-m1")
        assert recs.total == 1
        assert recs.items[0].result is not None


# ==========================================
# 2. UniversalToolRegistry 执行埋点集成
# ==========================================

class TestRegistryTraceIntegration:

    async def test_executed_tool_recorded(self, reg, backend):
        """配置后端 + context 含 agentId/traceId 时，工具执行完毕自动记录 success"""
        reg.set_trace_backend(backend)
        reset = _set_agent_context(agent_id="agent-1", trace_id="trace-1")
        try:
            @reg.register(category="订单")
            async def create_order(order_id: str):
                return {"orderId": order_id, "status": "PAID"}

            result = await reg.aexecute("create_order", {"order_id": "A100"})
        finally:
            reset()

        assert result == {"orderId": "A100", "status": "PAID"}
        recs = (await backend.get_trace("trace-1")).items
        assert len(recs) == 1
        rec = recs[0]
        assert rec.agent_id == "agent-1"
        assert rec.tool_name == "create_order"
        assert rec.args == {"order_id": "A100"}
        assert rec.result == {"orderId": "A100", "status": "PAID"}
        assert rec.status == "success"
        assert rec.extras == {}

    async def test_meta_tools_and_dispatchers_not_recorded(self, reg, backend):
        """list_categories/list_tools/get_tool_info 等元工具与列表/详情查询不产生记录"""
        reg.set_trace_backend(backend)
        reset = _set_agent_context(agent_id="agent-1", trace_id="trace-meta")
        try:
            @reg.register(category="订单")
            async def say_hi(name: str):
                return f"hi {name}"

            await reg.aexecute("list_categories")
            await reg.aexecute("list_tools", {"category": "订单"})
            await reg.aexecute("get_tool_info", {"name": "say_hi"})
            await reg.aexecute("say_hi", {"name": "Tom"})  # 唯一应记录的调用
        finally:
            reset()

        stats = await backend.get_stats("agent-1")
        assert stats["call_count"] == 1
        rec = (await backend.get_trace("trace-meta")).items[0]
        assert rec.tool_name == "say_hi"

    async def test_skip_without_ids_or_backend(self, reg, backend):
        """context 缺 agentId/traceId 或未配置后端时不产生任何记录"""
        # 无后端
        reset = _set_agent_context(agent_id="agent-1", trace_id="trace-x")
        try:
            @reg.register(category="订单")
            async def ping():
                return "pong"

            assert await reg.aexecute("ping") == "pong"
        finally:
            reset()
        assert (await backend.get_stats()).get("call_count", 0) == 0

        # 有后端但 context 无 agentId（只有 traceId）
        reg.set_trace_backend(backend)
        reset = _set_agent_context(trace_id="trace-y")
        try:
            assert await reg.aexecute("ping") == "pong"
        finally:
            reset()
        assert (await backend.get_stats()).get("call_count", 0) == 0
        # 有后端但 context 完全没有 ids
        reset = _set_agent_context()
        try:
            assert await reg.aexecute("ping") == "pong"
        finally:
            reset()
        assert (await backend.get_stats()).get("call_count", 0) == 0

    async def test_trace_disabled_switch(self, backend):
        """trace_enabled=False 时即使配置了后端与 context ids 也不产生记录"""
        reg = UniversalToolRegistry(trace_enabled=False)
        reg.set_trace_backend(backend)
        reg.add_category("订单", "订单管理模块")
        reset = _set_agent_context(agent_id="agent-1", trace_id="trace-off")
        try:
            @reg.register(category="订单")
            async def ping():
                return "pong"

            assert await reg.aexecute("ping") == "pong"
        finally:
            reset()

        assert (await backend.get_stats()).get("call_count", 0) == 0
        assert (await backend.get_trace("trace-off")).total == 0

    async def test_error_tool_recorded_with_error_status(self, reg, backend):
        """工具内部抛异常时返回错误文本，并记录 status=error 与错误内容"""
        reg.set_trace_backend(backend)
        reset = _set_agent_context(agent_id="agent-1", trace_id="trace-err")
        try:
            @reg.register(category="订单")
            async def boom():
                raise RuntimeError("db exploded")

            result = await reg.aexecute("boom")
        finally:
            reset()

        assert "工具执行失败" in result
        assert "db exploded" in result
        recs = await backend.get_trace("trace-err")
        assert recs.total == 1
        assert recs.items[0].status == "error"
        assert recs.items[0].extras == {}
        assert "db exploded" in recs.items[0].result

    async def test_validation_failure_not_recorded(self, reg, backend):
        """入参校验失败（业务函数未执行）时不记录"""
        reg.set_trace_backend(backend)
        reset = _set_agent_context(agent_id="agent-1", trace_id="trace-val")
        try:
            @reg.register(category="订单")
            async def create_user(age: Annotated[int, Field(ge=18, description="年龄")]):
                return f"Created age={age}"

            result = await reg.aexecute("create_user", {"age": 10})
        finally:
            reset()

        assert "入参校验失败" in result
        assert (await backend.get_trace("trace-val")).total == 0
