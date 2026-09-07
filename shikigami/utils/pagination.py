"""分页结果包装 PageResult（工具返回 / trace 查询共用）"""
import json
from typing import Generic, List, TypeVar

from .serialization import serialize_payload

T = TypeVar("T")


class PageResult(Generic[T]):
    """分页结果包装：items 为当前页数据，total 为满足条件的总数"""

    def __init__(self, total: int, items: List[T], page: int = 1, page_size: int = 10):
        self.items = items
        self.total = total
        self.page = page
        self.page_size = page_size
        self.total_page = ((total + page_size - 1) // page_size) if page_size > 0 else 0

    @property
    def current_page(self) -> int:
        """兼容别名：历史实现里 current_page/page/data 均为未定义属性，现统一到 page/items"""
        return self.page

    @property
    def data(self) -> List[T]:
        return self.items

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "totalPage": self.total_page,
            "currentPage": self.page,
            "pageSize": self.page_size,
            "data": [serialize_payload(item) for item in self.items]
        }

    def __str__(self) -> str:
        """工具返回/日志场景需要可读文本，默认 repr 只有对象地址"""
        try:
            text = json.dumps(self.to_dict(), ensure_ascii=False, default=str)
        except Exception:
            text = repr(getattr(self, "data", None))
        if len(text) > 6000:
            return f"{text[:6000]}...(共{self.total}条结果，内容过长已截断)"
        return text

    __repr__ = __str__
