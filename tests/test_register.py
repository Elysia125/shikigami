import dataclasses
from typing import Annotated, List

import pytest
from pydantic import BaseModel, ConfigDict, Field

from toolforge import ResourceParam, UniversalToolRegistry, context
from toolforge.register import parse_docstring, ContextParam

# 导入核心注册表及助手


# ==========================================
# 0. 测试用 DTO / Class 结构定义
# ==========================================

# 快捷类型别名
InjectUser = Annotated[dict, ContextParam("user")]
InjectTenant = Annotated[str, ContextParam(alias="tenant_id")]


# 任意非 Pydantic 的自定义资源类
class MockDBConnection:
    def __init__(self):
        self.is_closed = False

    def query(self, sql: str):
        if self.is_closed:
            raise RuntimeError("数据库连接已关闭，无法执行查询！")
        return f"[DB Result for: {sql}]"

    def close(self):
        self.is_closed = True


class MockRedisClient:
    def __init__(self):
        self.is_closed = False

    async def get(self, key: str):
        if self.is_closed:
            raise RuntimeError("Redis 连接已关闭！")
        return f"redis_val_{key}"

    async def close(self):
        self.is_closed = True


# 1. BaseModel 类型
class PydanticItem(BaseModel):
    user: InjectUser  # Context 注入
    item_name: str = Field(description="Pydantic商品名称")


# 2. Dataclass 类型
@dataclasses.dataclass
class DataclassItem:
    user: InjectUser  # Context 注入
    title: str
    price: float = 9.9


# 3. 普通 Class 类型
class PlainClassItem:
    def __init__(self, user: InjectUser, label: str, qty: int = 1):
        self.user = user
        self.label = label
        self.qty = qty


# 4. 深层嵌套 DTO (第 3 层)
class DeepDBConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    db: Annotated[MockDBConnection, ResourceParam("db")]  # Resource 注入
    timeout: int = Field(default=30, description="超时秒数")


# 4. 深层嵌套 DTO (第 2 层)
class DeepUserScope(BaseModel):
    user: InjectUser  # Context 注入
    db_config: DeepDBConfig


# 4. 深层嵌套 DTO (第 1 层顶层)
class DeepSearchRequest(BaseModel):
    keyword: Annotated[str, Field(description="搜索关键字")]
    scope: DeepUserScope


# 5. 包含三种 Class 列表的超复杂 DTO
class MixedBatchDTO(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    batch_code: str = Field(description="批次代码")
    bm_items: List[PydanticItem] = Field(description="Pydantic 列表")
    dc_items: List[DataclassItem] = Field(description="Dataclass 列表")
    plain_items: List[PlainClassItem] = Field(description="PlainClass 列表")


# ==========================================
# 1. Pytest 脚手架 (Fixtures)
# ==========================================

@pytest.fixture
def registry():
    """每个测试用例独立创建一个注册中心"""
    reg = UniversalToolRegistry()
    reg.add_category("订单", "订单管理模块")
    reg.add_category("用户", "用户管理模块")
    reg.add_category("系统", "系统诊断模块")
    return reg


@pytest.fixture
def setup_resources_and_context(registry):
    """设置生成器资源与 Context，测试结束后自动触发资源清理与 Context 重置"""
    db_conn = MockDBConnection()
    redis_conn = MockRedisClient()

    # 同步生成器资源
    def get_db():
        try:
            yield db_conn
        finally:
            db_conn.close()

    # 异步生成器资源
    async def get_redis():
        try:
            yield redis_conn
        finally:
            await redis_conn.close()

    registry.add_resource("db", get_db)
    registry.add_resource("redis", get_redis)

    # 设置 ContextVar
    token = context.set({
        "user": {"id": 10086, "name": "Alice"},
        "tenant_id": "TENANT_EXPERT_99"
    })

    yield {
        "db": db_conn,
        "redis": redis_conn,
        "user": {"id": 10086, "name": "Alice"}
    }

    # 还原 Context
    context.reset(token)


# ==========================================
# 2. 测试组 1: 参数描述提取与 AI Prompt 极简树可读性
# ==========================================

class TestDescriptionAndPromptBuilder:

    def test_docstring_parsing_styles(self):
        """验证 Google 风格和 Sphinx 风格的 Docstring 精准解析"""

        def google_func(a: int, b: str):
            """谷歌风格函数
            Args:
                a: 参数A的说明
                b: 参数B的说明
            """

        def sphinx_func(x: float):
            """Sphinx风格函数
            :param x: X浮点数说明
            """

        sum1, docs1 = parse_docstring(google_func)
        assert sum1 == "谷歌风格函数"
        assert docs1 == {"a": "参数A的说明", "b": "参数B的说明"}

        sum2, docs2 = parse_docstring(sphinx_func)
        assert sum2 == "Sphinx风格函数"
        assert docs2 == {"x": "X浮点数说明"}

    def test_prompt_building_readability_and_privacy(self, registry):
        """【关键】验证 Prompt 工具树格式清晰，且 Context/Resource 敏感字段 100% 被隐藏"""

        @registry.register(category="订单")
        async def query_order_sample(
                user: InjectUser,  # 应该被完全隐去！
                order_id: Annotated[str, Field(description="订单号")],
                status: Annotated[str, "订单状态：PAID/UNPAID"] = "ALL"
        ):
            """查询订单样例"""

        @registry.register(category="订单")
        async def complex_dto_sample(req: DeepSearchRequest):
            """复合 DTO 查询"""

        tool_info = registry.get_tool_info("query_order_sample")
        summary_tree = registry.build_summary_tree()

        # 4. 摘要树按分类聚合，参数与敏感字段隔离行为与 get_tool_info 一致
        assert "## 订单: 订单管理模块" in summary_tree
        assert "query_order_sample: 查询订单样例" in summary_tree
        assert "order_id: str [订单号]" in summary_tree
        assert "keyword: str [搜索关键字]" in summary_tree  # 深层 DTO 参数同样进入摘要树
        assert "user" not in summary_tree
        assert "db:" not in summary_tree

        # 1. 验证描述提取
        assert "订单号" in tool_info
        assert "订单状态：PAID/UNPAID" in tool_info
        assert "status: str?" in tool_info  # 可选参数带 ?

        # 2. 验证敏感字段彻底隔离（绝对不能在给 AI 的 Prompt 里出现 user 或 db）
        assert "user" not in tool_info
        assert "ContextParam" not in tool_info

        # 3. 验证深层 DTO Prompt 可读性（层级嵌套可读）
        deep_info = registry.get_tool_info("complex_dto_sample")
        assert "DeepSearchRequest_AIModel" in deep_info
        assert "DeepUserScope_AIModel" in deep_info
        assert "DeepDBConfig_AIModel" in deep_info
        assert "keyword: str [搜索关键字]" in deep_info
        assert "timeout: int? [超时秒数]" in deep_info
        assert "db:" not in deep_info  # 嵌套内部的 Resource 也被成功隐藏！


# ==========================================
# 3. 测试组 2: 资源生命周期管理 (生成器自动归还)
# ==========================================

class TestResourceLifecycle:

    @pytest.mark.asyncio
    async def test_generator_resources_lifecycle(self, registry, setup_resources_and_context):
        """验证工具执行前自动建立连接，工具执行后自动触发生成器的 finally 释放连接"""
        db_conn = setup_resources_and_context["db"]
        redis_conn = setup_resources_and_context["redis"]

        @registry.register(category="系统")
        async def health_check(
                db: Annotated[MockDBConnection, ResourceParam("db")],
                redis: Annotated[MockRedisClient, ResourceParam("redis")]
        ):
            # 在工具执行期间，连接必须处于可用状态
            assert not db.is_closed
            assert not redis.is_closed
            res = db.query("SELECT 1")
            val = await redis.get("ping")
            return f"{res} | {val}"

        # 执行前连接正常
        assert not db_conn.is_closed
        assert not redis_conn.is_closed

        res = await registry.aexecute("health_check", {})
        assert "DB Result for: SELECT 1" in res
        assert "redis_val_ping" in res

        # 执行完毕后，AsyncExitStack 应当已经自动调用生成器的 finally 块关掉了连接！
        assert db_conn.is_closed
        assert redis_conn.is_closed


# ==========================================
# 4. 测试组 3: 3 层深度嵌套 DTO 的上下文/资源重构
# ==========================================

class TestDeepNestedDTO:

    @pytest.mark.asyncio
    async def test_three_level_nested_dto_injection(self, registry, setup_resources_and_context):
        """验证 3 层深层嵌套 DTO 中散落的 ContextParam 和 ResourceParam 均被精准重构注入"""

        @registry.register(category="订单")
        async def deep_search(req: DeepSearchRequest):
            # 验证最外层、中间层、最深层的属性均正常初始化并注入
            user_name = req.scope.user["name"]
            timeout = req.scope.db_config.timeout
            db_res = req.scope.db_config.db.query(f"SEARCH {req.keyword}")
            return f"User={user_name}, Timeout={timeout}, Res={db_res}"

        # AI 传来的参数（完全不包含 user 和 db）
        ai_input = {
            "req": {
                "keyword": "MacBook Pro",
                "scope": {
                    "db_config": {
                        "timeout": 60
                    }
                }
            }
        }

        res = await registry.aexecute("deep_search", ai_input)
        assert "User=Alice" in res
        assert "Timeout=60" in res
        assert "DB Result for: SEARCH MacBook Pro" in res

    @pytest.mark.asyncio
    async def test_nested_dto_missing_context_key_feedback(self, registry):
        """Context 缺失时返回含缺失 key 的自纠文本（不得把 FieldInfo 哨兵注入成值）"""

        @registry.register(category="订单")
        async def deep_search(req: DeepSearchRequest):
            return req.scope.user["name"]

        res = await registry.aexecute("deep_search", {
            "req": {
                "keyword": "MacBook Pro",
                "scope": {"db_config": {"timeout": 60}},
            }
        })
        assert "工具执行失败" in res
        assert "Context 中缺少 key: 'user'" in res
        assert "用于 'DeepUserScope.user'" in res


# ==========================================
# 5. 测试组 4: 容器类型 (List[BaseModel], List[Dataclass], List[PlainClass])
# ==========================================

class TestContainerListInjection:

    @pytest.mark.asyncio
    async def test_mixed_class_lists_reconstruction(self, registry, setup_resources_and_context):
        """验证顶层及内部包含三种不同 Class (BaseModel/@dataclass/PlainClass) 的数组重构与 Context 注入"""

        @registry.register(category="订单")
        async def batch_process(dto: MixedBatchDTO):
            bm_names = [f"{i.item_name}({i.user['name']})" for i in dto.bm_items]
            dc_titles = [f"{i.title}:{i.price}({i.user['name']})" for i in dto.dc_items]
            plain_labels = [f"{i.label}x{i.qty}({i.user['name']})" for i in dto.plain_items]
            return f"Batch={dto.batch_code} | BM={bm_names} | DC={dc_titles} | PLAIN={plain_labels}"

        ai_input = {
            "dto": {
                "batch_code": "BATCH_2026_TEST",
                "bm_items": [{"item_name": "BM_Phone"}],
                "dc_items": [{"title": "DC_Pad", "price": 2999.0}],
                "plain_items": [{"label": "PLAIN_Pen", "qty": 5}],
            }
        }

        res = await registry.aexecute("process_mixed_batch", ai_input)

        # 改用具体调用的工具名称测试
        @registry.register(category="订单", name="run_batch")
        async def run_batch(dto: MixedBatchDTO):
            return await batch_process(dto)

        res = await registry.aexecute("run_batch", ai_input)

        assert "Batch=BATCH_2026_TEST" in res
        assert "BM_Phone(Alice)" in res
        assert "DC_Pad:2999.0(Alice)" in res
        assert "PLAIN_Penx5(Alice)" in res


# ==========================================
# 6. 测试组 5: 参数校验与自我修正反馈
# ==========================================

class TestValidationAndCoercion:

    @pytest.mark.asyncio
    async def test_pydantic_validation_error_feedback(self, registry, setup_resources_and_context):
        """验证当 AI 传入错误数据类型或违背约束条件时，格式化返回明确错误文本以供 AI 纠错"""

        @registry.register(category="用户")
        async def create_user(
                age: Annotated[int, Field(ge=18, le=100, description="年龄必须在 18-100 之间")],
                email: Annotated[str, Field(pattern=r"^[\w\.-]+@[\w\.-]+\.\w+$", description="合法的邮箱地址")]
        ):
            return f"Created age={age}"

        # 1. 触发 age < 18 的校验错误
        res1 = await registry.aexecute("create_user", {"age": 10, "email": "test@example.com"})
        assert "入参校验失败:" in res1
        assert "greater than or equal to 18" in res1

        # 2. 触发正则不匹配的校验错误
        res2 = await registry.aexecute("create_user", {"age": 20, "email": "invalid_email_format"})
        assert "入参校验失败:" in res2
        assert "String should match pattern" in res2

        # 3. 正常输入 (测试类型强转 "25" -> 25)
        res3 = await registry.aexecute("create_user", {"age": "25", "email": "valid@example.com"})
        assert res3 == "Created age=25"


# ==========================================
# 7. 测试组 6: 元工具与目录管理
# ==========================================

class TestMetaToolsAndCategories:

    @pytest.mark.asyncio
    async def test_meta_tools_chain(self, registry, setup_resources_and_context):
        """测试 list_categories, list_tools, get_tool_info 等全量元工具链路"""

        @registry.register(category="订单", description="订单查询工具")
        async def query_order_tool(status: str = "ALL"):
            return f"Status: {status}"

        # 1. list_categories
        cats = registry.list_categories()
        assert "订单: 订单管理模块" in cats

        # 2. list_tools
        tools = registry.list_tools("订单")
        assert "query_order_tool: 订单查询工具" in tools

        # 3. get_tool_info
        info = registry.get_tool_info("query_order_tool")
        assert "工具 'query_order_tool'" in info
        assert "status: str?" in info

        # 4. aexecute 执行通用元工具
        res_cat = await registry.aexecute("list_categories")
        assert "订单: 订单管理模块" in res_cat

        res_info = await registry.aexecute("get_tool_info", {"name": "query_order_tool"})
        assert "工具 'query_order_tool'" in res_info

        # 5. 测试 get_meta_tools (LangChain 工具导出)
        meta_tools = registry.get_meta_tools()
        assert len(meta_tools) == 4
        meta_tool_names = [t.name for t in meta_tools]
        assert "list_categories" in meta_tool_names
        assert "list_tools" in meta_tool_names
        assert "get_tool_info" in meta_tool_names
        assert "execute_tool" in meta_tool_names