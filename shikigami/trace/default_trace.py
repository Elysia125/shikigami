"""SQLAlchemy（asyncio）关系型数据库 Trace 后端实现。

兼容 MySQL / PostgreSQL / SQLite 及其它 SQLAlchemy 支持的数据库，切换数据库只需替换连接 URL：

- SQLite:     sqlite+aiosqlite:///./traces.db        （pip install aiosqlite / shikigami[sqlite]）
- MySQL:      mysql+asyncmy://user:pass@host:3306/db  （pip install asyncmy 或 aiomysql / shikigami[mysql]）
- PostgreSQL: postgresql+asyncpg://user:pass@host:5432/db（pip install asyncpg / shikigami[postgresql]）

表结构会在首次使用前自动创建（SQLite 会自动建库文件；MySQL/PostgreSQL 需预先建好 database，
账号需具备建表权限）。args/result/extras 落 JSON 列，任何非 JSON 原生对象会经 default=str 兜底后写入。
"""
import asyncio
import logging
import time
from typing import Any, Dict, Optional

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ..utils.serialization import jsonable

from .models import TraceRecord, TraceSummary
from ..utils.pagination import PageResult
from .trace import AbstractAgentTrace

log = logging.getLogger(__name__)

# 支持异步的 URL 前缀；同步驱动（pymysql/psycopg2/原生 sqlite 等）无法驱动 AsyncEngine
_ASYNC_URL_PREFIXES = (
    "sqlite+aiosqlite://",
    "mysql+asyncmy://",
    "mysql+aiomysql://",
    "postgresql+asyncpg://",
    "postgresql+psycopg://",
)


class SqlAlchemyTrace(AbstractAgentTrace):
    """基于 SQLAlchemy 2.x asyncio 的关系型数据库实现（AbstractAgentTrace 的全部抽象方法）。

    可通过 url 或已建好的 AsyncEngine 注入（engine 方式可复用外部连接池/事务配置）。
    """

    def __init__(self, url: Optional[str] = None, engine: Optional[AsyncEngine] = None,
                 table_name: str = "agent_trace_records", echo: bool = False):
        if (url is None) == (engine is None):
            raise ValueError("必须且只能提供 url 与 engine 其中之一")
        if url is not None and not url.startswith(_ASYNC_URL_PREFIXES):
            raise ValueError(
                f"需要 asyncio 驱动的连接 URL（如 sqlite+aiosqlite://、mysql+asyncmy://、"
                f"postgresql+asyncpg://），当前为: {url}。也可传入现成的 AsyncEngine 对象。")
        self._engine: AsyncEngine = create_async_engine(url, echo=echo) if url else engine
        metadata = sa.MetaData()
        self._table = sa.Table(
            table_name, metadata,
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("agent_id", sa.String(64), nullable=False, index=True),
            sa.Column("trace_id", sa.String(64), nullable=False, index=True),
            sa.Column("tool_name", sa.String(255), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("args", sa.JSON, nullable=True),
            sa.Column("result", sa.JSON, nullable=True),
            sa.Column("extras", sa.JSON, nullable=True),
            sa.Column("timestamp", sa.Integer, nullable=False, index=True),
        )
        self._metadata = metadata
        self._init_lock = asyncio.Lock()
        self._ready = False

    async def _ensure_tables(self) -> None:
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            async with self._engine.begin() as conn:
                await conn.run_sync(self._metadata.create_all)
            self._ready = True

    # 写入

    async def record(self, agent_id: str, trace_id: str, tool_name: str, args: dict, result: Any,
                     status: str = "success", extras: Optional[dict] = None) -> None:
        await self._ensure_tables()
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(self._table).values(
                agent_id=str(agent_id),
                trace_id=str(trace_id),
                tool_name=str(tool_name),
                status=str(status),
                args=jsonable(args),
                result=jsonable(result),
                extras=jsonable(extras or {}),
                timestamp=int(time.time()),
            ))

    # 查询

    async def get_trace(self, trace_id: str, page: int = 1, size: int = 10) -> PageResult[TraceRecord]:
        await self._ensure_tables()
        t = self._table
        where = t.c.trace_id == str(trace_id)
        size = max(1, int(size))
        offset = max(0, int(page) - 1) * size
        async with self._engine.connect() as conn:
            total = (await conn.execute(
                sa.select(sa.func.count()).select_from(t).where(where))).scalar_one()
            rows = (await conn.execute(
                sa.select(t).where(where)
                .order_by(t.c.timestamp.asc(), t.c.id.asc())
                .limit(size).offset(offset))).mappings().all()
        items = [
            TraceRecord(
                agent_id=row["agent_id"],
                trace_id=row["trace_id"],
                tool_name=row["tool_name"],
                args=row["args"] or {},
                result=row["result"],
                timestamp=int(row["timestamp"]),
                extras=row["extras"] or {},
                status=row["status"],
            )
            for row in rows
        ]
        return PageResult(total=int(total), items=items, page=max(1, int(page)), page_size=size)

    async def get_traces(self, agent_id: str, page: int = 1, page_size: int = 10) -> PageResult[TraceSummary]:
        await self._ensure_tables()
        t = self._table
        where = t.c.agent_id == str(agent_id)
        page_size = max(1, int(page_size))
        offset = max(0, int(page) - 1) * page_size
        count_subq = sa.select(t.c.trace_id).where(where).group_by(t.c.trace_id).subquery()
        async with self._engine.connect() as conn:
            total = (await conn.execute(
                sa.select(sa.func.count()).select_from(count_subq))).scalar_one()
            rows = (await conn.execute(
                sa.select(t.c.agent_id, t.c.trace_id,
                          sa.func.count().label("tool_count"),
                          sa.func.min(t.c.timestamp).label("start_time"),
                          sa.func.max(t.c.timestamp).label("end_time"))
                .where(where)
                .group_by(t.c.agent_id, t.c.trace_id)
                .order_by(sa.func.max(t.c.timestamp).desc(), t.c.trace_id.asc())
                .limit(page_size).offset(offset))).all()
        items = [
            TraceSummary(
                agent_id=row.agent_id,
                trace_id=row.trace_id,
                tool_count=int(row.tool_count),
                start_time=int(row.start_time),
                end_time=int(row.end_time),
            )
            for row in rows
        ]
        return PageResult(total=int(total), items=items, page=max(1, int(page)), page_size=page_size)

    async def get_stats(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        await self._ensure_tables()
        t = self._table
        filters = [t.c.agent_id == str(agent_id)] if agent_id is not None else []
        async with self._engine.connect() as conn:
            trace_count = (await conn.execute(
                sa.select(sa.func.count(sa.distinct(t.c.trace_id))).select_from(t).where(*filters))).scalar_one()
            call_count = (await conn.execute(
                sa.select(sa.func.count()).select_from(t).where(*filters))).scalar_one()
            tool_count = (await conn.execute(
                sa.select(sa.func.count(sa.distinct(t.c.tool_name))).select_from(t).where(*filters))).scalar_one()
        return {
            "agent_id": agent_id,
            "trace_count": int(trace_count),
            "call_count": int(call_count),
            "tool_count": int(tool_count),
        }

    # 删除

    async def delete_trace(self, trace_id: str) -> bool:
        await self._ensure_tables()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                sa.delete(self._table).where(self._table.c.trace_id == str(trace_id)))
        return res.rowcount > 0

    async def delete_agent_traces(self, agent_id: str) -> int:
        await self._ensure_tables()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                sa.delete(self._table).where(self._table.c.agent_id == str(agent_id)))
        return res.rowcount

    async def delete_before(self, timestamp: int) -> int:
        await self._ensure_tables()
        async with self._engine.begin() as conn:
            res = await conn.execute(
                sa.delete(self._table).where(self._table.c.timestamp < int(timestamp)))
        return res.rowcount

    # 生命周期

    async def close(self) -> None:
        await self._engine.dispose()
