"""资源生命周期引擎：统一 普通函数 / 同步生成器 / 异步生成器 / ContextManager 的进入与归还"""
import contextlib
import inspect
from typing import Any


async def acquire_resource(resource_getter: Any, exit_stack: contextlib.AsyncExitStack) -> Any:
    """支持普通函数、同步生成器、异步生成器、ContextManager 的生命周期统一管理"""
    if callable(resource_getter):
        if inspect.isasyncgenfunction(resource_getter):
            cm = contextlib.asynccontextmanager(resource_getter)()
            return await exit_stack.enter_async_context(cm)
        elif inspect.isgeneratorfunction(resource_getter):
            cm = contextlib.contextmanager(resource_getter)()
            return exit_stack.enter_context(cm)
        else:
            res = resource_getter()
            if inspect.isawaitable(res):
                res = await res
            if hasattr(res, "__aenter__"):
                return await exit_stack.enter_async_context(res)
            elif hasattr(res, "__enter__"):
                return exit_stack.enter_context(res)
            return res
    elif hasattr(resource_getter, "__aenter__"):
        return await exit_stack.enter_async_context(resource_getter)
    elif hasattr(resource_getter, "__enter__"):
        return exit_stack.enter_context(resource_getter)

    return resource_getter
