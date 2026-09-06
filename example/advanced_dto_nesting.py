#!/usr/bin/env python3
"""
任意深度嵌套 DTO 示例

三层嵌套 DTO（第 1 层 -> 第 3 层），Context / Resource 藏在最内层：
AI 传参时完全看不到 user / tenant / db，框架在执行前递归重构并注入。

运行: python example/advanced_dto_nesting.py
"""

import asyncio
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from toolforge import ContextParam, ResourceParam, context, default_registry, set_agent_context

# ---------- 便捷注入别名 ----------
InjectUser = Annotated[dict, ContextParam("user")]
InjectTenant = Annotated[str, ContextParam("tenant_id")]

default_registry.add_category("订单", "订单管理模块")


# ---------- 模拟带生命周期的数据库资源 ----------
class MockDB:
    def __init__(self):
        self.closed = False

    def query(self, sql: str) -> str:
        assert not self.closed, "数据库连接已关闭！"
        return f"[DB] {sql}"


def get_db():
    conn = MockDB()
    try:
        yield conn
    finally:
        conn.closed = True


default_registry.add_resource("db", get_db)


# =========================================================
# 第 3 层：Resource 藏在这里（db 字段 + 业务字段 timeout）
# =========================================================
class DbConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    db: Annotated[Any, ResourceParam("db")]                      # AI 不可见
    timeout: int = Field(default=30, description="查询超时(秒)")


# =========================================================
# 第 2 层：Context 藏在这里（user / tenant 字段）
# =========================================================
class UserScope(BaseModel):
    user: InjectUser                                            # AI 不可见
    tenant: InjectTenant                                        # AI 不可见
    db_config: DbConfig


# =========================================================
# 第 1 层：AI 直接传这层（只看到 keyword 和 scope 的骨架）
# =========================================================
class DeepSearchRequest(BaseModel):
    keyword: Annotated[str, Field(description="搜索关键词")]
    scope: UserScope


@default_registry.register(category="订单", description="深度检索订单（三层嵌套 DTO）")
async def deep_search(req: DeepSearchRequest):
    user = req.scope.user
    db = req.scope.db_config.db
    timeout = req.scope.db_config.timeout
    return (
        f"User={user['name']} tenant={req.scope.tenant} timeout={timeout} "
        f"| {db.query(f'SELECT * FROM orders WHERE kw={req.keyword}')}"
    )


async def main():
    # 1. 查看 AI 侧参数：user/tenant/db 全部被剥离
    token = context.set({
        "user": {"id": 1001, "name": "Alice", "role": "admin"},
        "tenant_id": "T-ACME-001",
    })
    try:
        print("=" * 60)
        print("【AI 可见参数（敏感字段已隐藏）】")
        print(default_registry.get_tool_info("deep_search"))
        print()
        print("【全量摘要树】")
        print(default_registry.build_summary_tree())

        # 2. 模拟 AI 传参：只传业务字段，框架自动递归注入 Context/Resource
        print("\n" + "=" * 60)
        print("【执行（AI 视角只传 keyword + scope 骨架）】")
        result = await default_registry.aexecute("deep_search", {
            "req": {
                "keyword": "MacBook",
                "scope": {"db_config": {"timeout": 60}},   # user/tenant/db 都不需要传
            },
        })
        print(result)
    finally:
        context.reset(token)

    # 3. 演示 Context 不完整时的“模型自纠反馈”：返回文本而非崩溃
    print("\n" + "=" * 60)
    print("【Context 缺少 user 再调用 -> 收到自纠文本（不中断回合）】")
    async with set_agent_context({"tenant_id": "T-ACME-001"}):   # 故意缺 user
        feedback = await default_registry.aexecute("deep_search", {
            "req": {"keyword": "MacBook", "scope": {"db_config": {}}},
        })
        print(feedback)


if __name__ == "__main__":
    asyncio.run(main())
