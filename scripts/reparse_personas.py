"""重新解析已有身份的人设，补上 F12 之前存下的身份。

    make reparse                 # 只处理开发账号（默认 admin、demo）
    make reparse ARGS=--all      # 处理全部身份（会真的花额度，慎用）
    make reparse ARGS="--account admin"

为什么需要它：F12 的解析发生在**保存身份时**。在此之前存下的身份（比如你自己改的
「林冲的姨夫」）没有声明边，prompt 里就还是那句「素不相识」——不是代码没生效，
是没人回头给它们解析一次。用户手改过的边（`is_manual`）不会被覆盖。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app import repository as repo  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.relation_parse import build_relation_parser, sync_declared_relations  # noqa: E402

DEFAULT_ACCOUNTS = ("admin", "demo")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="重新解析身份的人设关系")
    parser.add_argument("--account", action="append", default=[], help="账号 external_id，可重复")
    parser.add_argument("--all", action="store_true", help="处理全部身份（会花额度）")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    accounts: List[str] = args.account or list(DEFAULT_ACCOUNTS)
    parser = build_relation_parser(settings)

    await db.connect()
    try:
        async with db.pool().acquire() as conn:
            if args.all:
                rows = await conn.fetch(
                    """
                    SELECT p.* FROM personas p
                    WHERE NOT p.is_archived ORDER BY p.user_id, p.id
                    """
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT p.* FROM personas p
                    JOIN users u ON u.id = p.user_id
                    WHERE NOT p.is_archived AND u.external_id = ANY($1::text[])
                    ORDER BY p.user_id, p.id
                    """,
                    accounts,
                )
            if not rows:
                print("没有匹配的身份（用 --account 指定账号，或 --all 处理全部）")
                return

            print(f"解析器：{type(parser).__name__}　待处理身份：{len(rows)} 个")
            print()
            for persona in rows:
                stored = await sync_declared_relations(conn, dict(persona), parser)
                if stored:
                    detail = "；".join(
                        f"{item['character_name']}（{item['user_label']}／{item['character_label']}）"
                        for item in stored
                    )
                else:
                    detail = "没解析出关系（自述里没点名提到角色）"
                print(f"  {persona['name']:<8}{detail}")
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
