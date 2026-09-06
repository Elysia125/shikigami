"""注入标记与 Agent 上下文：ContextParam / ResourceParam / context(ContextVar) / set_agent_context"""
from contextlib import asynccontextmanager
from contextvars import ContextVar

# 请求级上下文（FastAPI 中间件 / Agent 启动处 set，工具执行处 get）
context: ContextVar[dict] = ContextVar("context", default={})


class ContextParam:
    """标记此类参数为后端上下文注入参数"""

    def __init__(self, alias: str = None, skip: bool = False):
        self.alias = alias
        self.skip = skip


class ResourceParam:
    """标记此类参数为资源注入参数"""

    def __init__(self, resource_name: str = None):
        self.resource_name = resource_name


@asynccontextmanager
async def set_agent_context(ctx_dict: dict):
    """助手函数：在任何异步代码块中安全设置与清理 Agent 上下文"""
    token = context.set(ctx_dict)
    try:
        yield
    finally:
        context.reset(token)
