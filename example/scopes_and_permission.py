#!/usr/bin/env python3
"""
Scope 权限控制 + set_agent_context 示例

1. 每个工具 / 元工具可声明 scopes（is_global=True 时自动附带 global）
2. 执行时按 context["scopes"] 与工具 scopes 是否相交来决定放行
3. set_agent_context 助手：一次性安全设置 / 清理上下文

运行: python example/scopes_and_permission.py
"""

import asyncio

from shikigami import ContextParam, context, default_registry, set_agent_context
from typing import Annotated

InjectUser = Annotated[dict, ContextParam("user")]

default_registry.add_category("系统", "系统诊断模块")
default_registry.add_category("管理", "管理后台")

# ---------- 三种作用域的工具 ----------
@default_registry.register(category="系统", description="全局公开查询")          # is_global 默认 True
async def public_query():
    return "公共查询结果 OK"


@default_registry.register(category="管理", scopes={"admin"}, is_global=False, description="清理缓存")
async def admin_reset_cache():
    return "缓存已清理"


@default_registry.register(category="管理", scopes={"user"}, is_global=False, description="查看我的配额")
async def user_quota(user: InjectUser):
    return f"用户 {user['name']} 剩余配额 80%"


# ---------- 元工具同样受 scopes 约束 ----------
@default_registry.meta_tool(name="admin_overview", scopes={"admin"}, is_global=False,
                    description="管理总览（admin）")
async def admin_overview():
    return "[admin] 在线人数: 12, 待处理工单: 3"


async def main():
    # ---------- 场景 1：无任何 scopes（默认只带 global） ----------
    print("=" * 60)
    print("【场景1：空上下文（默认仅 global 作用域）】")
    async with set_agent_context({}):
        print("public_query   =>", await default_registry.aexecute("public_query", {}))
        print("admin_reset    =>", await default_registry.aexecute("admin_reset_cache", {}))
        print("user_quota     =>", await default_registry.aexecute("user_quota", {}))
        print("admin_overview =>", await default_registry.aexecute("admin_overview"))

    # ---------- 场景 2：普通用户（scopes={user, global}） ----------
    print("\n" + "=" * 60)
    print("【场景2：普通用户 scopes={user, global}】")
    async with set_agent_context({
        "user": {"id": 7, "name": "Bob"},
        "scopes": {"user", "global"},
    }):
        print("public_query   =>", await default_registry.aexecute("public_query", {}))
        print("user_quota     =>", await default_registry.aexecute("user_quota", {}))
        print("admin_reset    =>", await default_registry.aexecute("admin_reset_cache", {}))
        print("admin_overview =>", await default_registry.aexecute("admin_overview"))

    # ---------- 场景 3：管理员（scopes={admin, global}） ----------
    print("\n" + "=" * 60)
    print("【场景3：管理员 scopes={admin, global}】")
    async with set_agent_context({
        "user": {"id": 1, "name": "Alice", "role": "admin"},
        "scopes": {"admin", "global"},
    }):
        print("public_query   =>", await default_registry.aexecute("public_query", {}))
        print("user_quota     =>", await default_registry.aexecute("user_quota", {}))
        print("admin_reset    =>", await default_registry.aexecute("admin_reset_cache", {}))
        print("admin_overview =>", await default_registry.aexecute("admin_overview"))

    # ---------- 场景 4：上下文越权校验（管理员冒充普通用户视角） ----------
    print("\n" + "=" * 60)
    print("【场景4：set_agent_context 退出后上下文自动还原为空】")
    print("退出作用域后 public_query =>",
          await default_registry.aexecute("public_query", {}))   # 仍可执行（global）
    print("退出作用域后 admin_reset  =>",
          await default_registry.aexecute("admin_reset_cache", {}))  # 无权限拒绝


if __name__ == "__main__":
    asyncio.run(main())
