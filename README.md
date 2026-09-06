# 🔥 ToolForge — 企业级 Agent 工具注册与执行框架

> **极简 Token · 零越权 · 自动资源回收 · 深度 DTO 嵌套**

**ToolForge** 专为 LLM Agent 生产落地打造，一揽子解决 **Token 费用暴涨**、**`user_id` 伪造越权**、**数据库/Redis 连接泄漏** 以及 **复杂 DTO 开发繁琐** 四大硬伤。

---

## 📦 安装

```bash
pip install toolforge          # 基础版（pydantic 即可运行）
pip install "toolforge[langchain]"     # 需要导出 LangChain StructuredTool 时
pip install "toolforge[nest-asyncio]"  # 需要在异步环境内调用 default_registry.execute 时
```

从源码开发调试：

```bash
git clone <repo-url>
pip install -e ".[dev,langchain,nest-asyncio]"
```

---

## ✨ 核心亮点

| 痛点 | ToolForge 解决方案 |
| :--- | :--- |
| 💸 **Token 消耗过大** | 内置元工具（`list_tools`、`get_tool_info`）实现“按需加载”，告别一次性塞入数百个 JSON Schema |
| 🔓 **IDOR 越权** | 使用 `@ContextParam` 标记敏感参数，对 AI **绝对不可见**，由后端通过 `contextvars` 强制注入 |
| 🔌 **连接泄漏** | 支持同步/异步生成器（`yield`）资源，工具执行后自动 `finally` 释放，保护连接池 |
| 📦 **复杂 DTO 开发** | 原生支持 **Pydantic `BaseModel`**、**`@dataclass`** 和 **普通 Class**，且支持 **任意深度嵌套** 及 **`List[DTO]` 批量注入** |
| 🧩 **多源描述** | 自动提取 `Field()`、`Annotated` 文本、Google/Sphinx Docstring 中的参数说明 |
| 🔗 **LangChain 集成** | 直接导出 `StructuredTool` 列表，无缝接入 LangGraph / ReAct Agent |
| 🎯 **细粒度权限** | 通过 `scopes` 划分工具可见范围，支持全局/租户/用户级隔离 |
| ⚙️ **执行钩子** | 提供 `before_execute` / `after_execute` 拦截器，统一日志、审计、限流 |
| 🗂️ **全局工具总览** | `build_summary_tree()` 按分类生成全量工具摘要树，调试与路由一目了然 |

---

## 🚀 快速上手

```python
import asyncio
from typing import Annotated

from pydantic import Field

from toolforge import ContextParam, context, default_registry

# 1. 定义"后端注入"类型别名：带 ContextParam 标记的参数对 AI 完全不可见
InjectUser = Annotated[dict, ContextParam("user")]

# 2. 注册分类
default_registry.add_category("订单", "订单管理")

# 3. 注册工具（user 参数由后端 contextvars 强制注入，不进入 AI Schema）
@default_registry.register(category="订单", description="查询用户订单")
async def query_orders(
    user: InjectUser,  # AI 看不到这个参数
    status: Annotated[str, Field(description="状态: PAID/UNPAID")] = "ALL",
):
    return f"用户 [{user['name']}] 的 {status} 订单: [ORD_1001]"

# 4. 模拟执行
async def main():
    context.set({"user": {"id": 101, "name": "Alice"}})

    # 查看 AI 侧参数（user 已被剥离）
    print(default_registry.get_tool_info("query_orders"))
    # 实际输出：
    #   工具 'query_orders'。
    #   描述: 查询用户订单
    #   参数: (status: str? [状态: PAID/UNPAID])

    # 执行（AI 只需传 status）
    result = await default_registry.aexecute("query_orders", {"status": "PAID"})
    print(result)  # 用户 [Alice] 的 PAID 订单: [ORD_1001]

asyncio.run(main())
```

> 更多**可直接运行**的完整示例见 `example/` 目录（`python example/xxx.py`），从浅到深覆盖全部能力：

| 示例文件 | 演示内容 |
| :--- | :--- |
| `quick_start.py` | 注册分类/工具、AI 侧 Schema、异步/同步执行、ContextParam 隐藏 |
| `advanced_dto_nesting.py` | 三层嵌套 DTO，Context/Resource 藏在最内层，含缺失反馈演示 |
| `batch_list_dto.py` | 一个 DTO 混装 BaseModel / `@dataclass` / 普通类三种列表并批量注入 |
| `resource_lifecycle_and_hooks.py` | 同步/异步生成器资源自动释放、`before/after_execute` 钩子、钩子异常降级 |
| `scopes_and_permission.py` | 工具/元工具 Scope 权限隔离、`set_agent_context` 上下文隔离 |
| `meta_tools_and_langchain.py` | 自定义元工具（显式 Schema / 自动推导 + ContextParam 绑定）、`get_meta_tools` 导出 StructuredTool |

---

## 📝 参数定义的 8 种形态（全覆盖）

### ① `Annotated + Field`（推荐，强校验 + 描述）

```python
@default_registry.register(...)
async def func(
    score: Annotated[int, Field(ge=0, le=100, description="成绩")],
):
    ...
```

### ② 原生 `Field`（免 `Annotated`）

```python
async def func(
    limit: int = Field(default=10, ge=1, description="分页大小")
):
    ...
```

### ③ `Annotated` 纯文本（极简）

```python
async def func(
    order_id: Annotated[str, "订单号，如 ORD_123"]
):
    ...
```

### ④ Docstring 提取（零侵入，兼容旧代码）

```python
@default_registry.register(...)
async def search(keyword: str, page: int = 1):
    """搜索用户
    Args:
        keyword: 关键词
        page: 页码
    """
    ...
```

### ⑤ 上下文参数 `@ContextParam`（绝对隐藏）

```python
InjectUser = Annotated[dict, ContextParam("user")]
InjectTenant = Annotated[str, ContextParam]  # 默认按形参名取

async def func(user: InjectUser, tenant: InjectTenant):
    ...
```

### ⑥ 资源注入 `@ResourceParam`（自动生命周期）

```python
async def func(
    db: Annotated[Any, ResourceParam("db")],
    redis: Annotated[Any, ResourceParam("redis")],
):
    ...
```

### ⑦ 复合 DTO（支持 **三种类定义** + 任意嵌套）

#### Pydantic `BaseModel`
```python
from pydantic import BaseModel

class Address(BaseModel):
    city: str = Field(description="城市")
    street: str = Field(description="街道")

class UserDTO(BaseModel):
    user: InjectUser                     # 上下文注入
    address: Address                     # 嵌套 DTO
    db: Annotated[Any, ResourceParam("db")]  # 资源注入
```

#### Python `@dataclass`
```python
import dataclasses

@dataclasses.dataclass
class Item:
    name: str
    qty: int = 1
```

#### 普通 Class（Plain Class）
```python
class Product:
    def __init__(self, title: str, price: float = 0.0):
        self.title = title
        self.price = price
```

#### 深度嵌套与 `List[DTO]`
```python
from pydantic import BaseModel, ConfigDict

class OrderDTO(BaseModel):
    # 仅当 DTO 字段直接包含普通类时需要放开该配置（@dataclass / BaseModel 嵌套无需）
    model_config = ConfigDict(arbitrary_types_allowed=True)
    user: InjectUser
    items: List[Item]      # List[dataclass] 自动注入
    product: Product       # 普通类

@default_registry.register(...)
async def create_order(order: OrderDTO):
    # order.user 已注入，order.items 中每个 Item 的字段已就绪
    ...
```
> 框架递归处理所有嵌套层级，列表中的每个元素也会被递归注入上下文/资源。

### ⑧ `List[DTO]` 批量容器
```python
@default_registry.register(...)
async def batch_process(orders: List[OrderDTO]):
    ...
```
每个 `OrderDTO` 内部的所有上下文/资源都会被自动填充。

---

## 🔒 上下文注入与安全隔离（FastAPI 中间件）

```python
from fastapi import FastAPI, Request
from toolforge import context

app = FastAPI()

@app.middleware("http")
async def agent_context(request: Request, call_next):
    user = parse_jwt(request.headers["Authorization"])
    token = context.set({
        "user": user,
        "tenant": request.headers.get("X-Tenant-ID", "default"),
        "scopes": {"user:" + user["id"]}  # 用于权限过滤
    })
    try:
        return await call_next(request)
    finally:
        context.reset(token)
```

> 非 FastAPI 的异步/脚本场景，可用 `set_agent_context(ctx)` 助手（`async with` 自动 set/reset）完成同样的事，见 `example/scopes_and_permission.py`。

---

## 🔌 资源生命周期管理（连接池保护）

注册资源获取函数（支持普通函数、同步/异步生成器、上下文管理器）：

```python
def get_db():
    db = pool.get_conn()
    try:
        yield db
    finally:
        db.close()

async def get_redis():
    redis = await redis.asyncio.from_url("redis://localhost/0")  # 或任意 async 客户端
    try:
        yield redis
    finally:
        await redis.aclose()

default_registry.add_resource("db", get_db)
default_registry.add_resource("redis", get_redis)

@default_registry.register(...)
async def query(db: Annotated[Any, ResourceParam("db")], key: str):
    # 使用后自动释放
    return db.execute(...)
```

---

## ⚙️ 前置/后置执行器（钩子）

统一拦截所有工具调用，用于日志、审计、限流、重试等。

```python
async def before(tool_name: str, args: dict):
    print(f"准备执行 {tool_name}，参数 {args}")

async def after(tool_name: str, args: dict):
    print(f"执行完成 {tool_name}")

default_registry.set_before_execute(before)
default_registry.set_after_execute(after)
```

---

## 🎯 Scope 划分（细粒度权限控制）

注册工具时可指定 `scopes` 集合，调用时只暴露匹配 scope 的工具：

```python
@default_registry.register(..., scopes={"admin", "global"})
async def admin_tool(...): ...

@default_registry.register(..., scopes={"user"})
async def user_tool(...): ...

# 在上下文中注入当前用户的 scopes
context.set({"scopes": {"user", "tenant:123"}})

# 执行时，只有匹配 scope 的工具可被调用（元工具也受 scope 限制）
await default_registry.aexecute("admin_tool", {})  # 失败，无权限
```

元工具同样支持 `scopes` 和 `is_global` 参数。

---

## 🛠️ 自定义元工具（Meta Tools）

除内置 `list_categories`、`list_tools`、`get_tool_info`、`execute_tool` 外，你可以扩展自己的元工具：

```python
from pydantic import BaseModel, Field

class SearchSchema(BaseModel):
    q: str = Field(description="查询关键词")

@default_registry.meta_tool(arg_schema=SearchSchema, description="搜索工具")
async def search_meta(q: str):
    return f"搜索结果: {q}..."
```

---

## 🔗 LLM & LangChain 深度集成

`default_registry.get_meta_tools()` 导出**固定 4 个** `StructuredTool`（`list_categories` / `list_tools` / `get_tool_info` / `execute_tool`），把"加载哪些工具、怎么传参"变成可查询的元能力，让 AI 按需拉取参数，极大减少一次性塞入全部 Schema 的 Token 开销：

```python
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from toolforge import default_registry

meta_tools = default_registry.get_meta_tools()  # 4 个固定 StructuredTool

prompt = ChatPromptTemplate.from_messages([
    ("system", (
        "你是一个助手。可用工具类别：\n"
        f"{default_registry.list_categories()}\n"
        "不确定参数时先用 get_tool_info(name) 查询，所有业务工具通过 execute_tool 执行。"
    )),
    ("human", "{input}"),
])

agent = create_tool_calling_agent(ChatOpenAI(model="gpt-4o-mini"), meta_tools, prompt)
executor = AgentExecutor(agent=agent, tools=meta_tools)
```

导出时可注入当前请求上下文（含 `ContextParam` 的元工具在此完成绑定，避免 AI 会话内传身份）：

```python
# 把 user 预绑定进元工具闭包（按别名 key 匹配）
meta_tools = default_registry.get_meta_tools(
    scopes={"global", "user:" + user["id"]},
    user=user,                    # ContextParam("user") 取这里的值
)
```

---

## 📖 API 参考

| 方法 | 说明 |
| :--- | :--- |
| `add_category(cat, desc)` | 添加分类 |
| `add_resource(name, getter)` | 注册资源（支持生成器/ContextManager） |
| `register(category, description, name, scopes, is_global)` | 业务工具装饰器 |
| `meta_tool(arg_schema, name, description, scopes, is_global)` | 元工具装饰器 |
| `get_tool_info(name)` | 获取单个工具参数 Schema（AI 侧，敏感字段已剥离） |
| `build_summary_tree()` | 按分类生成全量工具摘要树（工具名/描述/参数） |
| `aexecute(tool_name, tool_args)` | 异步执行工具（拦截、校验、注入、回收；失败返回文本供 AI 自纠，不抛异常） |
| `execute(tool_name, tool_args)` | 同步执行（自动适配事件循环，异步环境需 `nest-asyncio`） |
| `get_meta_tools(scopes, **kwargs)` | 导出 LangChain `StructuredTool` 列表，`kwargs` 可预绑定上下文参数 |
| `set_before_execute(func)` / `set_after_execute(func)` | 设置执行钩子（同步/异步均可） |
| `set_agent_context(ctx_dict)` | async 上下文管理器，安全地设置/清理 Agent 上下文 |
| `list_categories()` / `list_tools(category)` | 内置元工具 |

---

## 🧱 目录结构（开发者）

单文件核心已按职责拆分到 `registry/` 子包，可通过改造各模块实现自定义扩展（如替换 `schema_builder` 定制参数解析、继承 `UniversalToolRegistry` 覆写钩子）；`register.py` 是向后兼容的转发层，旧 import 路径保持不变。

```
toolforge/
├── __init__.py              # 顶层导出（default_registry / context / ContextParam / ...）
├── register.py              # 兼容转发层：from toolforge.register import ... 仍可用
└── registry/
    ├── __init__.py          # 子包汇总导出
    ├── markers.py           # ContextParam / ResourceParam / context / set_agent_context
    ├── docstring.py         # Docstring 解析（Sphinx / Google / NumPy）
    ├── introspection.py     # 类型注解与反射分析
    ├── resources.py         # 资源生命周期引擎（acquire_resource）
    ├── class_meta.py        # DTO Class 递归解析与重构引擎
    ├── schemas.py           # 固定元工具 Schema
    ├── schema_builder.py    # 函数签名 -> AI 输入 Schema 编译器
    └── registry.py          # UniversalToolRegistry 主类 + 默认单例
example/                     # 可直接运行的示例（见上文表格）
tests/                       # pytest 测试
```

---

## 🧪 运行测试

```bash
python -m pytest tests/
```

---

## 📄 许可证

本项目基于 [MIT 许可证](LICENSE) 协议开源。详细许可内容请参阅项目根目录下的 [LICENSE](LICENSE) 文件，或参考 [MIT 官方开源协议说明](https://opensource.org/licenses/MIT)。

---

## 🌟 为何选择 ToolForge？

- ✅ **安全第一**：敏感参数永不落盘、永不进 LLM 上下文。
- ✅ **开发效率**：一行装饰器 + 类型注解，自动生成 AI 可用的参数描述。
- ✅ **生产就绪**：自动资源回收、死锁重试、钩子拦截、细粒度权限。
- ✅ **生态友好**：与 FastAPI、LangChain 无缝配合。

---

**立即体验，让 Agent 开发更优雅、更安全、更省钱！** 🚀