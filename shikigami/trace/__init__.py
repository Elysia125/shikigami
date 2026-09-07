"""Shikigami Trace 子包：Agent 工具调用追踪的抽象接口与数据库实现。

- trace                 抽象接口 AbstractAgentTrace（+ 全局约定）
- models                TraceRecord / TraceSummary 数据模型
- default_trace         基于 SQLAlchemy(asyncio) 的关系型数据库实现（SQLite / MySQL / PostgreSQL / ...）

sqlalchemy 为可选依赖：不安装时本包仍可正常导入（可自定义 AbstractAgentTrace 实现），
仅 SqlAlchemyTrace 不可用。安装方式：pip install shikigami[sqlite]（或 [mysql] / [postgresql]）。
"""
from .models import TraceRecord, TraceSummary
from .trace import AbstractAgentTrace

__all__ = [
    "AbstractAgentTrace",
    "TraceRecord",
    "TraceSummary"
]

try:
    import sqlalchemy  # noqa: F401
except ImportError:  # sqlalchemy 未安装：库本体可用，仅默认数据库实现不可用
    pass
else:
    from .default_trace import SqlAlchemyTrace

    __all__.append("SqlAlchemyTrace")


def __getattr__(name: str):
    if name == "SqlAlchemyTrace":
        raise ImportError(
            "shikigami.trace.SqlAlchemyTrace 需要 sqlalchemy（可选依赖）："
            "请执行 pip install shikigami[sqlite]（或 [mysql] / [postgresql]）")
    raise AttributeError(f"module 'shikigami.trace' has no attribute '{name}'")
