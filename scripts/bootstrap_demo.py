"""准备一个可以直接开聊的演示环境。

  1. 建演示用户「demo」并写好用户人设
  2. 把时间线拨到第十回（林冲风雪山神庙）
  3. 建一个与林冲的一对一会话
  4. 打印可以直接复制的 curl 命令

用法：.venv/bin/python scripts/bootstrap_demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app import repository as repo  # noqa: E402
from app.config import settings  # noqa: E402

DEMO_CHAPTER_NO = 10  # 第十回 林教头风雪山神庙


async def main() -> None:
    await db.connect()
    try:
        async with db.pool().acquire() as conn:
            work = await repo.get_work_by_slug(conn, settings.default_work_slug)
            if work is None:
                raise SystemExit("数据库里没有《水浒传》，请先执行 scripts/dev_db.sh")

            user = await repo.ensure_user(conn, "demo", "演示用户")
            await repo.upsert_persona(
                conn,
                user["id"],
                work["id"],
                name="张三",
                identity="东京城里开酒铺的掌柜，常年与江湖上的人打交道",
                background="祖上在东京卖酒，自己识得几个字，会些拳脚，算不上好汉",
                appearance="中等身量，穿一件半旧的青布直裰",
                speech_style="说话客气中带点精明，喜欢先问清楚对方的来历",
            )

            anchors = await repo.list_anchors(conn, work["id"])
            target = next((a for a in anchors if a["chapter_no"] == DEMO_CHAPTER_NO), None)
            if target is None:
                raise SystemExit(f"找不到第 {DEMO_CHAPTER_NO} 回对应的时间锚点")
            await repo.set_current_anchor(conn, user["id"], work["id"], target["id"])

            characters = await repo.get_characters_by_slugs(conn, work["id"], ["lin-chong"])
            if not characters:
                raise SystemExit("找不到林冲，请确认已导入 db/seed.sql")
            lin_chong = characters[0]

            existing = await conn.fetchrow(
                """
                SELECT s.id FROM sessions s
                JOIN session_members m
                  ON m.session_id = s.id AND m.member_kind = 'character' AND m.member_id = $2
                WHERE s.user_id = $1 AND s.session_type = 'direct' AND s.archived_at IS NULL
                ORDER BY s.id LIMIT 1
                """,
                user["id"],
                lin_chong["id"],
            )
            if existing:
                session_id = existing["id"]
                action = "复用已有会话"
            else:
                session = await repo.create_session(
                    conn,
                    user_id=user["id"],
                    work_id=work["id"],
                    session_type="direct",
                    title=f"与{lin_chong['name']}说话",
                    character_ids=[lin_chong["id"]],
                    created_anchor_id=target["id"],
                )
                session_id = session["id"]
                action = "新建会话"

        print(f"演示用户 id = {user['id']}")
        print(f"时间线     = {target['chapter_label']}《{target['name']}》")
        print(f"会话 id    = {session_id}（{action}）")
        print()
        print("看这个会话此刻的 system prompt：")
        print(f"  curl -s localhost:8000/api/sessions/{session_id}/prompt-preview | python3 -m json.tool")
        print()
        print("说一句话：")
        print(
            f"""  curl -s -X POST localhost:8000/api/sessions/{session_id}/messages \\
    -H 'Content-Type: application/json' \\
    -d '{{"content":"林教头，你这雪夜赶路，是要往哪里去？"}}' | python3 -m json.tool"""
        )
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
