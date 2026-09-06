#!/usr/bin/env python3
"""
Shikigami 快速上手示例

展示核心功能：
1. 注册工具分类
2. 注册一个业务工具（含 ContextParam 隐藏参数）
3. 设置请求上下文（模拟用户身份注入）
4. 查看 AI 侧参数 Schema（验证敏感参数已隐藏）
5. 执行工具（AI 只需提供业务参数）
"""

import asyncio
from typing import Annotated

from pydantic import Field

# 从 Shikigami 导入核心 API
from shikigami import default_registry, context, ContextParam


async def main():
    # ---------- 1. 注册分类 ----------
    default_registry.add_category("订单", "订单管理模块")

    # ---------- 2. 注册业务工具 ----------
    @default_registry.register(category="订单", description="查询当前用户的订单列表")
    async def query_orders(
        user: Annotated[dict, ContextParam] = None,  # 对 AI 完全隐藏，后端自动注入
        status: Annotated[str, Field(description="订单状态: PAID / UNPAID / ALL")] = "ALL",
        limit: Annotated[int, Field(ge=1, le=100, description="返回条数")] = 10,
    ):
        """模拟查询订单，返回包含用户信息的示例数据"""
        user_name = user.get("name", "未知用户")
        return {
            "user": user_name,
            "status": status,
            "orders": [f"ORD-{i:04d}" for i in range(1, limit + 1)],
            "count": limit,
        }

    # ---------- 3. 设置请求上下文（通常由 FastAPI 中间件完成） ----------
    # 模拟从 JWT/Header 解析出的用户信息
    fake_user = {"id": 1001, "name": "Alice", "role": "admin"}
    context.set({"user": fake_user})

    # ---------- 4. 查看工具信息（AI 侧参数） ----------
    print("=" * 60)
    print("【AI 可见的工具参数】")
    print(default_registry.get_tool_info("query_orders"))
    # 输出应不包含 'user' 字段，只有 status 和 limit

    # ---------- 5. 模拟 AI 调用工具 ----------
    print("\n" + "=" * 60)
    print("【执行工具（AI 只传业务参数）】")
    result = await default_registry.aexecute(
        "query_orders",
        {"status": "PAID", "limit": 5}
    )
    print("执行结果:")
    print(result)

    # ---------- 6. 展示同步执行方式（内部自动适配事件循环） ----------
    print("\n" + "=" * 60)
    print("【同步执行示例】")
    # 注意：在异步函数中调用同步执行时，内部会使用 nest_asyncio 处理
    # 但更推荐在同步环境中直接使用 default_registry.execute(...)
    sync_result = default_registry.execute("query_orders", {"status": "ALL"})
    print(sync_result)

    # ---------- 7. 清理（可选） ----------
    context.set({})  # 清空上下文，避免影响后续测试


if __name__ == "__main__":
    asyncio.run(main())