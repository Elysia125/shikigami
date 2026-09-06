#!/usr/bin/env python3
"""
资源生命周期 + before/after 钩子示例

1. 同步生成器 / 异步生成器资源：执行结束自动触发 finally 释放（连接不泄漏）
2. before_execute / after_execute 钩子：统一日志、审计、限流入口
3. 钩子抛异常时返回“自纠文本”而不是让整个 Agent 崩溃

运行: python example/resource_lifecycle_and_hooks.py
"""

import asyncio
from typing import Annotated, Any

from toolforge import ResourceParam, context, default_registry

default_registry.add_category("系统", "系统诊断模块")

# 记录钩子调用顺序
events: list[str] = []


# ---------- 资源定义 ----------
class MockDBConnection:
    def __init__(self):
        self.closed = False

    def query(self, sql: str):
        if self.closed:
            raise RuntimeError("数据库连接已关闭！")
        return f"[DB] {sql}"


class MockRedisClient:
    def __init__(self):
        self.closed = False

    async def get(self, key: str):
        if self.closed:
            raise RuntimeError("Redis 连接已关闭！")
        return f"redis_val_{key}"

    async def close(self):
        self.closed = True


def get_db():
    """同步生成器资源：finally 在工具执行完自动执行"""
    conn = MockDBConnection()
    try:
        yield conn
    finally:
        conn.closed = True


async def get_redis():
    """异步生成器资源：async finally 同样自动执行"""
    client = MockRedisClient()
    try:
        yield client
    finally:
        await client.close()
        client.closed = True


default_registry.add_resource("db", get_db)
default_registry.add_resource("redis", get_redis)


# ---------- 业务工具：一次性注入两种资源 ----------
@default_registry.register(category="系统", description="健康检查（依赖 DB + Redis 资源）")
async def health_check(
    db: Annotated[Any, ResourceParam("db")],
    redis: Annotated[Any, ResourceParam("redis")],
):
    # 工具执行期间资源必然处于可用状态
    return f"{db.query('SELECT 1')} | {await redis.get('ping')}"


@default_registry.register(category="系统", description="同步两数相加工具")
def add_numbers(a: int = 1, b: int = 2):
    return a + b


# ---------- 钩子 ----------
async def before_hook(tool_name: str, args: dict):
    events.append(f"before:{tool_name}")


def after_hook(tool_name: str, args: dict):
    """钩子也支持同步函数"""
    events.append(f"after:{tool_name}")


async def main():
    default_registry.set_before_execute(before_hook)
    default_registry.set_after_execute(after_hook)

    # ---------- 1. 生命周期：每次执行新建连接，执行完自动释放 ----------
    print("=" * 60)
    print("【1. 资源自动注入与释放】")
    print("调用 1:", await default_registry.aexecute("health_check", {}))
    print("调用 2:", await default_registry.aexecute("health_check", {}))
    # 两次独立执行，钩子各触发一轮
    assert events == ["before:health_check", "after:health_check"] * 2, events
    print("钩子事件顺序:", events)

    # ---------- 2. 同步执行（default_registry.execute 自动适配事件循环） ----------
    print("\n" + "=" * 60)
    print("【2. 同步 execute（在 async 环境内自动 nest_asyncio）】")
    result = default_registry.execute("add_numbers", {"a": 3, "b": 4})
    print("add_numbers(3,4) =", result)
    assert result == 7

    # ---------- 3. 钩子抛异常 -> 返回自纠文本，Agent 回合不中断 ----------
    print("\n" + "=" * 60)
    print("【3. after_execute 钩子抛异常时的降级】")
    import logging
    logging.getLogger("toolforge").disabled = True   # 屏蔽框架异常日志，聚焦降级文本
    try:
        default_registry.set_after_execute(lambda name, args: 1 / 0)
        print(await default_registry.aexecute("add_numbers", {"a": 1, "b": 1}))
    finally:
        logging.getLogger("toolforge").disabled = False

    # ---------- 4. 展示生成器 finally 的释放机制 ----------
    print("\n" + "=" * 60)
    print("【4. 连接回收验证】")
    gen = get_db()
    conn = next(gen)
    assert not conn.closed
    gen.close()  # 触发生成器 finally -> close()
    assert conn.closed
    print("生成器资源手动 close 后已释放:", conn.closed)

    # 恢复钩子，避免影响其他示例
    default_registry.set_before_execute(None)
    default_registry.set_after_execute(None)
    context.set({})
    print("\n示例全部通过")


if __name__ == "__main__":
    asyncio.run(main())
