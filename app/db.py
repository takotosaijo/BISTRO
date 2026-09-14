from __future__ import annotations

import json
from typing import Optional

import asyncpg

from app.config import settings

_pool: Optional[asyncpg.Pool] = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """asyncpg 默认把 json/jsonb 当字符串返回，这里注册成 Python 对象。

    world_state、address_forms、sample_lines、milestones 都依赖它，
    少了这一步 prompt 会静默退化成「天下纷纷，一时也说不清」。
    """

    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def connect(dsn: Optional[str] = None) -> asyncpg.Pool:
    """建立连接池。重复调用返回同一个池。"""

    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn or settings.asyncpg_dsn, min_size=1, max_size=8, init=_init_connection
        )
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("数据库连接池尚未初始化，请先 await connect()")
    return _pool
