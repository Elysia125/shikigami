"""UniversalToolRegistry 主类：工具/元工具/资源注册、Schema 缓存、执行引擎（含 MySQL 死锁重试）"""
import asyncio
import contextlib
import inspect
import logging
from functools import wraps
from typing import Any, Callable, Dict, Set, Type

from pydantic import BaseModel, ValidationError

from ..trace import AbstractAgentTrace
from ..utils.serialization import jsonable, serialize_payload
from .class_meta import format_type_for_prompt, reconstruct_instance
from .docstring import parse_docstring
from .introspection import is_undefined
from .markers import context
from .resources import acquire_resource
from .schemas import EmptySchema, ToolExecuteSchema, ToolInfoSchema, ToolsSchema
from .schema_builder import SignatureSchemaBuilder

log = logging.getLogger(__name__)


def _is_deadlock(exc: BaseException) -> bool:
    """死锁判定：遍历异常链（sqlalchemy 包装 asyncmy 原始错误）命中 Deadlock 字样"""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if "Deadlock" in str(exc):
            return True
        exc = exc.__cause__
    return False


# 通用工具注册中心
class UniversalToolRegistry:
    TOOL_EXECUTE_NAME = "execute_tool"

    def __init__(self, trace_enabled: bool = True):
        """创建注册中心。

        :param trace_enabled: 执行追踪总开关，默认开启。关闭后即使配置了 trace 后端，工具执行也不会产生任何记录。
        """
        self._trace_enabled = trace_enabled
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._categories: Dict[str, Any] = {}
        self._resources: Dict[str, Callable] = {}
        self._meta_tools: Dict[str, Any] = {}
        self._scopes: Dict[str, Any] = {}
        self._before_execute: Callable = None
        self._after_execute: Callable = None
        self._trace: AbstractAgentTrace = None
        self._schema_builder = SignatureSchemaBuilder()

    def set_before_execute(self, func: Callable):
        self._before_execute = func

    def set_after_execute(self, func: Callable):
        self._after_execute = func

    def set_trace_backend(self, trace: AbstractAgentTrace):
        """配置执行追踪后端（shikigami.trace.AbstractAgentTrace 的实现）。

        记录规则：
        - 需构造时 trace_enabled=True（默认开启），且已配置后端，才会产生记录；
        - 仅 register 注册的业务工具在 aexecute 中执行完毕后写入；
        - 元工具（list_categories / list_tools / get_tool_info / execute_tool 与 meta_tool 注册的函数）不记录；
        - agentId / traceId 从 context 读取（无则回退 agent_id / trace_id），缺任一即不记录；
        - trace 写入失败仅记日志、不影响工具结果。
        """
        self._trace = trace

    def add_resource(self, resource_name: str, get_resource: Callable):
        self._resources[resource_name] = get_resource

    def add_category(self, category: str, description: str):
        self._categories[category] = {"name": category, "description": description}

    @staticmethod
    def _bind_context_params(func, bind_params):
        """把上下文参数绑定为闭包。langgraph 会用 get_type_hints 解析工具函数，
        直接传 functools.partial 会抛 "is not a module, class, method, or function"，
        且原函数签名（可能含 Annotated 泛型）会带进解析，故用 *args/**kwargs 包装后隐藏。"""
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def wrapped(*args, **kwargs):
                return await func(*args, **bind_params, **kwargs)

            return wrapped

        @wraps(func)
        def wrapped(*args, **kwargs):
            return func(*args, **bind_params, **kwargs)

        return wrapped

    def meta_tool(self, arg_schema: type[BaseModel] | dict[str, Any] | None = None, name: str = None,
                  description: str = None, scopes: Set[str] = (), is_global: bool = True):
        """
        注册元工具函数
        """

        def decorator(func: Callable):
            tool_name = name or func.__name__
            if arg_schema is not None:
                func_summary, _ = parse_docstring(func)
                final_schema = arg_schema
                context_params_map = {}
            else:
                built = self._schema_builder.build(func, tool_name, allow_resource=False)
                final_schema = built.schema
                context_params_map = built.context_params_map
                func_summary = built.summary
            final_description = description or func_summary or "无描述"
            is_async = inspect.iscoroutinefunction(func)
            sps = set(scopes)
            if is_global:
                sps.add("global")
            self._meta_tools[tool_name] = {
                "coroutine": func if is_async else None,
                "func": func if not is_async else None,
                "schema": final_schema,
                "name": tool_name,
                "description": final_description,
                "context_params_map": context_params_map,
                "scopes": sps,
            }
            for scope in sps:
                if scope in self._scopes:
                    self._scopes[scope]["meta_tools"].append(tool_name)
                else:
                    self._scopes[scope] = {
                        "tools": [],
                        "meta_tools": [tool_name]
                    }
            return func

        return decorator

    def register(self, category: str, description: str = None, name: str = None, scopes: Set[str] = (),
                 is_global: bool = True):
        if category not in self._categories:
            raise ValueError(f"Category '{category}' 不存在！")

        def decorator(func: Callable):
            tool_name = name or func.__name__
            built = self._schema_builder.build(func, tool_name)
            final_description = description or built.summary or "无描述"
            is_async = inspect.iscoroutinefunction(func)
            sps = set(scopes)
            if is_global:
                sps.add("global")
            self._tools[tool_name] = {
                "func": func,
                "category": category,
                "description": final_description,
                "model": built.schema,
                "context_params_map": built.context_params_map,
                "resource_params_map": built.resource_params_map,
                "class_info_map": built.class_info_map,
                "is_async": is_async,
                "scopes": sps,
            }
            for scope in sps:
                if scope in self._scopes:
                    self._scopes[scope]["tools"].append(tool_name)
                else:
                    self._scopes[scope] = {
                        "tools": [tool_name],
                        "meta_tools": []
                    }
            return func

        return decorator

    # --- 元工具函数定义 ---
    def list_categories(self) -> str:
        """列出所有工具类别"""
        return "\n".join([f"- {cat['name']}: {cat['description']}" for cat in self._categories.values()])

    def list_tools(self, category: str) -> str:
        """列出指定类别的所有工具"""
        if category not in self._categories:
            return f"类别 '{category}' 不存在！"
        tools_list = [f"- {name}: {meta['description']}" for name, meta in self._tools.items() if
                      meta["category"] == category]
        if not tools_list:
            return f"类别 '{category}' 下暂无工具。"
        return "\n".join(tools_list)

    def _format_params(self, dynamic_model: Type[BaseModel]) -> str:
        """把 AI 输入模型格式化为 '参数名: 类型[描述]' 的紧凑文本（Context/Resource 字段已剥离）"""
        params_desc = []
        for f_name, f_info in dynamic_model.model_fields.items():
            type_repr = format_type_for_prompt(f_info.annotation)
            is_optional = not f_info.is_required()
            opt_str = "?" if (is_optional and not type_repr.endswith("?")) else ""
            desc_str = f" [{f_info.description}]" if f_info.description else ""
            params_desc.append(f"{f_name}: {type_repr}{opt_str}{desc_str}")
        return ", ".join(params_desc) if params_desc else "无参数"

    def get_tool_info(self, name: str) -> str:
        """获取指定工具的详细参数 Schema 信息"""
        meta = self._tools.get(name)
        if meta:
            return f"工具 '{name}'。\n描述: {meta['description']}\n参数: ({self._format_params(meta['model'])})"
        return f"工具 '{name}' 不存在！"

    def build_summary_tree(self) -> str:
        """按分类构建全量工具摘要树（工具名/描述 + AI 可见参数），供 Agent 一次性纵览整个工具面"""
        sections = []
        for category, cat_meta in self._categories.items():
            lines = [f"## {category}: {cat_meta['description']}"]
            cat_tools = [(tool_name, meta) for tool_name, meta in self._tools.items()
                         if meta["category"] == category]
            if not cat_tools:
                lines.append("- (暂无工具)")
            for tool_name, meta in cat_tools:
                lines.append(f"- {tool_name}: {meta['description']}")
                lines.append(f"    参数: ({self._format_params(meta['model'])})")
            sections.append("\n".join(lines))
        return "\n\n".join(sections) if sections else "（暂无任何分类）"

    def get_meta_tools(self, scopes: Set[str] = ("global",), **kwargs) -> list:
        """获取供 LangChain / Agent 使用的固定元工具列表"""
        from langchain_core.tools import StructuredTool

        meta_tools = [
            StructuredTool(
                name=self.list_categories.__name__,
                description=self.list_categories.__doc__,
                func=self.list_categories,
                args_schema=EmptySchema,
            ),
            StructuredTool(
                name=self.list_tools.__name__,
                description=self.list_tools.__doc__,
                func=self.list_tools,
                args_schema=ToolsSchema,
            ),
            StructuredTool(
                name=self.get_tool_info.__name__,
                description=self.get_tool_info.__doc__,
                func=self.get_tool_info,
                args_schema=ToolInfoSchema,
            ),
            StructuredTool(
                name=self.TOOL_EXECUTE_NAME,
                description=self.aexecute.__doc__,
                coroutine=self.aexecute,
                args_schema=ToolExecuteSchema,
            ),
        ]
        names = set()
        for scope in scopes:
            names.update(self._scopes.get(scope, {}).get("meta_tools", []))
        for tool_name in names:
            tool_meta = self._meta_tools[tool_name]
            func = tool_meta["func"]
            coroutine = tool_meta["coroutine"]
            context_params_map = tool_meta["context_params_map"]
            bind_params = {}
            for param_name, ctx_info in context_params_map.items():
                key = ctx_info.get("alias", None) or param_name
                if key in kwargs:
                    bind_params[key] = kwargs[key]
                elif ctx_info.get("default", None) is not None:
                    bind_params[key] = ctx_info["default"]
                elif ctx_info.get("optional", False) or ctx_info.get("is_skip", False):
                    bind_params[key] = None
                else:
                    raise KeyError(f"Missing required parameter: '{key}'")
            if bind_params:
                # langgraph ToolNode 用 get_type_hints 解析工具签名，partial 会报
                # "is not a module, class, method, or function"；改用闭包绑定上下文参数
                if func:
                    func = UniversalToolRegistry._bind_context_params(func, bind_params)
                if coroutine:
                    coroutine = UniversalToolRegistry._bind_context_params(coroutine, bind_params)
            meta_tools.append(
                StructuredTool(
                    name=tool_meta["name"],
                    description=tool_meta["description"],
                    func=func,
                    coroutine=coroutine,
                    args_schema=tool_meta["schema"],
                )
            )
        return meta_tools

    # --- 核心拦截与参数处理逻辑 ---
    async def _prepare_kwargs(self, tool_name: str, ai_raw_json: Dict[str, Any],
                              exit_stack: contextlib.AsyncExitStack) -> tuple[Callable, dict, bool]:
        meta = self._tools[tool_name]
        dynamic_model = meta["model"]

        validated_data = dynamic_model.model_validate(ai_raw_json)
        final_kwargs = {}
        cxt = context.get()

        # 1. 递归还原 Class 参数（支持普通 Class / DTO 内部包含 List[BaseModel/Dataclass/PlainClass]）
        for field in dynamic_model.model_fields:
            val = getattr(validated_data, field)
            # LLM 可能对带默认值的可选参数显式传 null，回填默认值避免 service 层收到 None
            if val is None:
                field_default = dynamic_model.model_fields[field].default
                if field_default is not None and not is_undefined(field_default):
                    val = field_default
            if field in meta["class_info_map"]:
                cls_meta = meta["class_info_map"][field]
                final_kwargs[field] = await reconstruct_instance(cls_meta, val, self._resources, exit_stack)
            else:
                final_kwargs[field] = val

        # 2. 注入顶层 ContextParam
        for param_name, ctx_info in meta["context_params_map"].items():
            if ctx_info.get("is_skip", False):
                continue
            key = ctx_info.get("alias", None) or param_name
            if key in cxt:
                final_kwargs[param_name] = cxt[key]
            elif (ctx_default := ctx_info.get("default")) is not None and ctx_default is not inspect.Parameter.empty:
                final_kwargs[param_name] = ctx_default
            elif ctx_info.get("optional", False):
                continue
            else:
                raise KeyError(f"Context 中缺少 key: '{key}'")

        # 3. 注入顶层 ResourceParam
        for param_name, res_info in meta["resource_params_map"].items():
            r_name = res_info["resource_name"]
            if r_name in self._resources:
                getter = self._resources[r_name]
                final_kwargs[param_name] = await acquire_resource(getter, exit_stack)
            elif (res_default := res_info.get("default")) is not None and res_default is not inspect.Parameter.empty:
                final_kwargs[param_name] = res_default
            elif res_info.get("optional", False):
                continue
            else:
                raise ValueError(f"Resource '{r_name}' 未注册")

        return meta["func"], final_kwargs, meta["is_async"]

    async def _record_execution(self, tool_name: str, tool_args: Any, result: Any, success: bool) -> None:
        """业务工具执行完毕后写 trace：未开启追踪 / 未配置后端 / context 缺 agentId、traceId 时静默跳过"""
        if not self._trace_enabled or self._trace is None:
            return
        cxt = context.get()
        agent_id = cxt.get("agentId") or cxt.get("agent_id")
        trace_id = cxt.get("traceId") or cxt.get("trace_id")
        if not agent_id or not trace_id:
            return
        try:
            args_data = jsonable(serialize_payload(tool_args))
            result_data = jsonable(serialize_payload(result))
            if args_data is None or result_data is None:
                log.error("工具 %s 的 trace 负载无法序列化，已跳过记录", tool_name)
                return
            await self._trace.record(
                agent_id, trace_id, tool_name, args_data, result_data,
                status="success" if success else "error")
        except Exception:
            log.error("工具 %s 执行完毕但 trace 记录失败", tool_name, exc_info=True)

    async def aexecute(self, tool_name: str, tool_args: Dict[str, Any] = {}) -> Any:
        """执行具体的业务工具或元工具逻辑"""
        if tool_name == self.list_categories.__name__:
            return self.list_categories()
        if tool_name == self.list_tools.__name__:
            return self.list_tools(tool_args.get("category", ""))
        if tool_name == self.get_tool_info.__name__:
            return self.get_tool_info(tool_args.get("name", ""))
        scopes = set(context.get().get("scopes", ("global",)))
        if tool_name in self._meta_tools:
            if all(scope not in self._meta_tools[tool_name]["scopes"] for scope in scopes):
                return f"无权限执行元工具 '{tool_name}'"
            coroutine = self._meta_tools[tool_name]["coroutine"]
            func = self._meta_tools[tool_name]["func"]
            if coroutine:
                return await coroutine(**tool_args)
            elif func:
                return func(**tool_args)
            else:
                raise ValueError(f"元工具 '{tool_name}' 无 coroutine 和 func，无法执行")
        if tool_name not in self._tools:
            return f"工具 '{tool_name}' 不存在！"
        elif all(scope not in self._tools[tool_name]["scopes"] for scope in scopes):
            return f"无权限执行工具 '{tool_name}'"
        if self._before_execute:
            try:
                if inspect.iscoroutinefunction(self._before_execute):
                    await self._before_execute(tool_name, tool_args)
                else:
                    self._before_execute(tool_name, tool_args)
            except Exception as e:
                log.error("工具 %s 调用前置处理函数异常", tool_name, exc_info=(type(e), e, e.__traceback__))
                return f"工具调用前置处理函数异常: {e}"
        # LangGraph 同轮并行执行多个写工具时，各事务行锁交叉会被 InnoDB 检测为 1213 死锁并回滚。
        # 每次重试都重建 exit_stack（新 db session + 新事务），死锁回滚后无残留副作用。
        max_attempts = 3
        backoff = 0.05
        executed = False  # 是否真正执行过业务函数（决定失败返回是否写入 trace）
        for attempt in range(max_attempts):
            try:
                async with contextlib.AsyncExitStack() as exit_stack:
                    try:
                        func, final_kwargs, is_async = await self._prepare_kwargs(tool_name, tool_args, exit_stack)
                    except ValidationError as e:
                        errors_list = []
                        for err in e.errors():
                            loc_path = ".".join(map(str, err['loc']))
                            errors_list.append(f"字段 '{loc_path}': {err['msg']}")
                        error_msg = "; ".join(errors_list)
                        return f"入参校验失败: {error_msg}"

                    executed = True
                    if is_async:
                        result = await func(**final_kwargs)
                    else:
                        result = func(**final_kwargs)
                    if self._after_execute:
                        try:
                            if inspect.iscoroutinefunction(self._after_execute):
                                await self._after_execute(tool_name, tool_args)
                            else:
                                self._after_execute(tool_name, tool_args)
                        except Exception as e:
                            log.error("工具 %s 调用后置处理函数异常", tool_name, exc_info=(type(e), e, e.__traceback__))
                            err_text = f"工具调用后置处理函数异常: {e}"
                            await self._record_execution(tool_name, tool_args, err_text, False)
                            return err_text
                    await self._record_execution(tool_name, tool_args, result, True)
                    return result
            except Exception as e:
                if _is_deadlock(e) and attempt < max_attempts - 1:
                    log.warning("工具 %s 触发死锁，%.0fms 后重试第 %d/%d 次",
                                tool_name, backoff * 1000, attempt + 1, max_attempts)
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                # 业务异常（乐观锁拒绝/策略拒绝/校验失败等）与重试耗尽：转文本返回模型自纠，
                # 避免单个工具失败 raise 崩掉整个 agent 回合；真人 HTTP 调用不经此路径不受影响
                log.error("工具 %s 执行异常", tool_name, exc_info=(type(e), e, e.__traceback__))
                if _is_deadlock(e):
                    err_text = f"工具执行失败（数据库死锁，重试{max_attempts}次仍冲突）: {e}"
                else:
                    err_text = f"工具执行失败: {e}"
                if executed:
                    await self._record_execution(tool_name, tool_args, err_text, False)
                return err_text
        return None

    def execute(self, tool_name: str, tool_args: Dict[str, Any] = {}) -> Any:
        """同步环境下的通用执行入口"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(self.aexecute(tool_name, tool_args))
        else:
            return asyncio.run(self.aexecute(tool_name, tool_args))


default_registry = UniversalToolRegistry()
