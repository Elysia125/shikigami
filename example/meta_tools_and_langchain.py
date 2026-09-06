#!/usr/bin/env python3
"""
自定义元工具 + LangChain StructuredTool 导出示例

内置 4 个固定元工具之外，可注册自己的元工具：
1. 显式 arg_schema（如 stat_range，给出 Schema 即由 AI 前端自动解析）
2. 函数签名自动推导 Schema + ContextParam 绑定（如 whoami，后端身份注入）
3. default_registry.get_meta_tools(scopes=..., **ctx) 导出为 langchain StructuredTool 列表

运行: python example/meta_tools_and_langchain.py
（未安装 langchain-core 时自动跳过 StructuredTool 导出演示）
"""

import asyncio
from typing import Annotated

from pydantic import BaseModel, Field

from toolforge import ContextParam, default_registry, set_agent_context

InjectUser = Annotated[dict, ContextParam("user")]

default_registry.add_category("订单", "订单管理模块")
default_registry.add_category("报表", "数据报表类")


# ---------- 普通业务工具（由 AI 经 execute_tool 间接调用） ----------
@default_registry.register(category="订单", description="查询当前用户的订单")
async def query_orders(user: InjectUser, status: str = "ALL"):
    return f"{user['name']} 的 {status} 订单: [ORD-1001, ORD-1002]"


@default_registry.register(category="订单", description="创建订单")
def create_order(user: InjectUser, amount: float = 99.0):
    return f"为 {user['name']} 创建金额 {amount} 的订单成功"


# ---------- 自定义元工具 1：显式 arg_schema（回归分析场景，参数约束交给 Schema） ----------
class StatRangeSchema(BaseModel):
    days: int = Field(default=7, ge=1, le=90, description="统计天数(1-90)")


@default_registry.meta_tool(arg_schema=StatRangeSchema, name="stat_range", scopes={"admin"},
                    description="按天统计报表（仅 admin 作用域）")
async def stat_range(days: int = 7):
    return f"[报表] 近 {days} 天订单数: 128 笔，GMV ￥42,000"


# ---------- 自定义元工具 2：签名自动推导 + ContextParam（身份由导出端预绑定） ----------
@default_registry.meta_tool(scopes={"admin"}, description="查看当前操作人")
async def whoami(user: InjectUser):
    return f"当前元工具调用者: {user['name']} (role={user['role']})"


async def main():
    admin_user = {"id": 1, "name": "Alice", "role": "admin"}

    # ---------- 全流程置于“管理员会话”上下文内 ----------
    async with set_agent_context({"user": admin_user, "scopes": {"global", "admin"}}):
        print("=" * 60)
        print("【1. aexecute 调用内置/自定义元工具】")
        print(default_registry.list_categories())
        print(default_registry.list_tools("订单"))
        # 注：execute_tool 只是 LangChain 导出时的固定入口（见第 3 步）；
        # 直接在 aexecute 走业务工具名即可
        print(await default_registry.aexecute("create_order", {"amount": 199.0}))
        print(await default_registry.aexecute("stat_range", {"days": 30}))
        # meta 工具签名里的 ContextParam 在 aexecute 路径不会自动注入（无 _prepare_kwargs 拦截）
        try:
            await default_registry.aexecute("whoami")
        except TypeError as e:
            print("whoami 直接调用抛 TypeError（meta 的 ContextParam 需导出时预绑定）:", e)

        # ---------- 2. 仅 user 作用域时调用 admin 元工具 -> 被拒 ----------
        print("\n" + "=" * 60)
        print("【2. 仅 user 作用域时调用 admin 元工具 -> 拒绝】")
        async with set_agent_context({"user": {"id": 9, "name": "Eve"}, "scopes": {"user"}}):
            print(await default_registry.aexecute("stat_range", {"days": 7}))

        # ---------- 3. langchain StructuredTool 导出 ----------
        print("\n" + "=" * 60)
        print("【3. get_meta_tools 导出（ContextParam 在导出时绑定）】")
        try:
            from langchain_core.tools import StructuredTool  # noqa: F401
        except ModuleNotFoundError:
            print("(未安装 langchain-core，跳过 StructuredTool 导出演示)")
            return

        tools = default_registry.get_meta_tools(scopes={"global", "admin"}, user=admin_user)
        by_name = {t.name: t for t in tools}
        print("导出工具名:", list(by_name))
        print()
        print("- list_categories =>", await by_name["list_categories"].ainvoke({}))
        print("- get_tool_info   =>", (await by_name["get_tool_info"].ainvoke({"name": "query_orders"})).splitlines()[2])
        print("- execute_tool    =>", await by_name["execute_tool"].ainvoke(
            {"tool_name": "query_orders", "tool_args": {"status": "PAID"}}))
        print("- stat_range      =>", await by_name["stat_range"].ainvoke({"days": 14}))
        print("- whoami          =>", await by_name["whoami"].ainvoke({}))


if __name__ == "__main__":
    asyncio.run(main())
