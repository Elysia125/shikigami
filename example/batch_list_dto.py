#!/usr/bin/env python3
"""
List[DTO] 批量容器示例

同一个 DTO 参数里混装三种类：Pydantic BaseModel / @dataclass / 普通 Class，
框架逐个元素递归重构，列表里每个元素的 Context 字段都会自动注入。

运行: python example/batch_list_dto.py
"""

import asyncio
import dataclasses
from typing import Annotated, List

from pydantic import BaseModel, ConfigDict, Field

from shikigami import ContextParam, context, default_registry

InjectUser = Annotated[dict, ContextParam("user")]

default_registry.add_category("订单", "订单管理模块")


# ---------- 1. BaseModel 元素 ----------
class PydanticItem(BaseModel):
    user: InjectUser                                    # Context 注入
    item_name: str = Field(description="商品名")


# ---------- 2. @dataclass 元素 ----------
@dataclasses.dataclass
class DataclassItem:
    user: InjectUser                                    # Context 注入
    title: str
    price: float = 9.9


# ---------- 3. 普通 Class 元素（非 Pydantic） ----------
class PlainClassItem:
    def __init__(self, user: InjectUser, label: str, qty: int = 1):
        self.user = user
        self.label = label
        self.qty = qty


# ---------- 混合三种元素列表的顶层 DTO ----------
class MixedBatchDTO(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    batch_code: str = Field(description="批次代码")
    bm_items: List[PydanticItem] = Field(description="Pydantic 元素列表")
    dc_items: List[DataclassItem] = Field(description="Dataclass 元素列表")
    plain_items: List[PlainClassItem] = Field(description="普通类元素列表")


@default_registry.register(category="订单", description="混合三种类的批量入库")
async def run_batch(dto: MixedBatchDTO):
    bm = [f"{i.item_name}({i.user['name']})" for i in dto.bm_items]
    dc = [f"{i.title}:{i.price}({i.user['name']})" for i in dto.dc_items]
    plain = [f"{i.label}x{i.qty}({i.user['name']})" for i in dto.plain_items]
    return f"Batch={dto.batch_code} | BM={bm} | DC={dc} | PLAIN={plain}"


async def main():
    token = context.set({"user": {"id": 10086, "name": "Alice"}})
    try:
        # 1. 查看 AI 可见 Schema：嵌套层级可读，user 字段不可见
        print("=" * 60)
        print("【AI 可见参数（三种类列表）】")
        print(default_registry.get_tool_info("run_batch"))

        # 2. 模拟 AI 传参：每个元素只需要业务字段
        print("\n" + "=" * 60)
        print("【执行（元素里的 user 由框架自动注入）】")
        result = await default_registry.aexecute("run_batch", {
            "dto": {
                "batch_code": "BATCH_2026",
                "bm_items": [{"item_name": "BM_Phone"}, {"item_name": "BM_Pad"}],
                "dc_items": [{"title": "DC_Keyboard", "price": 199.0}],
                "plain_items": [{"label": "PLAIN_Pen", "qty": 5}, {"label": "PLAIN_Paper", "qty": 2}],
            },
        })
        print(result)

        # 3. 校验注入确实生效（List 里的每个元素都拿到了 user）
        assert "BM_Phone(Alice)" in result and "BM_Pad(Alice)" in result
        assert "DC_Keyboard:199.0(Alice)" in result
        assert "PLAIN_Penx5(Alice)" in result and "PLAIN_Paperx2(Alice)" in result
        print("\n校验通过：列表中每个元素均完成 Context 注入")
    finally:
        context.reset(token)


if __name__ == "__main__":
    asyncio.run(main())
