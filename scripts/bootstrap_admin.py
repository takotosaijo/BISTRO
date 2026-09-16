"""开发用：在同一个账号下建几个身份，方便在试验台上切着看「不同的人设会怎样」。

    make admin

一个账号（external_id = `admin`），三个身份（幂等，重复跑只更新不重复建）：

  张三   东京城里开酒铺的掌柜          与角色素不相识的普通人
  玉娆   林冲失散多年的私生女          人设自带关系
  苏娘   林冲在东京时的旧相识          人设自带关系

每个身份预置一个与林冲的一对一会话（时间线拨到第十回）。这三个身份同时也是
F12（人设语义 → 结构化关系）的验收样本：前一个该解析成「无关系」，后两个该解析出关系。
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app import repository as repo  # noqa: E402
from app.config import settings  # noqa: E402

ACCOUNT = "admin"
CHAPTER_NO = 10  # 第十回 林教头风雪山神庙
CHARACTER_SLUG = "lin-chong"

IDENTITIES = [
    {
        "name": "张三",
        "identity": "东京城里开酒铺的掌柜，常年与江湖上的人打交道",
        "background": "祖上在东京卖酒，自己识得几个字，会些拳脚，算不上好汉",
        "appearance": "中等身量，穿一件半旧的青布直裰",
        "speech_style": "说话客气中带点精明，喜欢先问清楚对方的来历",
        "expect": "与林冲素不相识：只靠聊天推进关系",
    },
    {
        "name": "玉娆",
        "identity": "林冲失散多年的私生女，随母姓",
        "background": "母亲临终前才说出父亲是谁，如今一路寻他，只凭一句「东京八十万禁军枪棒教头」",
        "appearance": "十七八岁的姑娘，眉眼有几分像林冲",
        "speech_style": "话不多，问得直接，认死理",
        "expect": "人设自带关系：用户这一侧一开始就认他，角色那一侧要靠对话相认",
    },
    {
        "name": "苏娘",
        "identity": "林冲在东京时的旧相识，当年未及提亲他就出了事",
        "background": "高俅构陷之后两人再无音信，如今听说他刺配沧州，一路追来",
        "appearance": "二十多岁，一身素净衣裳，风尘仆仆",
        "speech_style": "称呼随意，带点旧日的亲近，说到伤心处会停住不说",
        "expect": "人设自带关系：角色那一侧本来就该是熟人",
    },
]


async def upsert_persona(
    conn, user_id: int, work_id: int, spec: Dict[str, Any]
) -> Dict[str, Any]:
    """按**身份语义**（identity）找已有的身份；有就更新，没有就新建。

    不按名字匹配是因为同一个名字下可能有不同身份（用户自己建过「玉娆 / 林冲的情人」，
    样板里还有「玉娆 / 私生女」），按名字会互相覆盖。
    """

    existing = await repo.list_personas(conn, user_id, work_id)
    match = next((p for p in existing if (p["identity"] or "") == spec["identity"]), None)
    fields = dict(
        name=spec["name"],
        identity=spec["identity"],
        background=spec["background"],
        appearance=spec["appearance"],
        speech_style=spec["speech_style"],
    )
    if match:
        return await repo.update_persona(conn, match["id"], **fields)
    return await repo.create_persona(conn, user_id, work_id, **fields)


async def main() -> None:
    await db.connect()
    try:
        async with db.pool().acquire() as conn:
            work = await repo.get_work_by_slug(conn, settings.default_work_slug)
            if work is None:
                raise SystemExit("数据库里没有《水浒传》，请先执行 make db")

            anchors = await repo.list_anchors(conn, work["id"])
            anchor = next((a for a in anchors if a["chapter_no"] == CHAPTER_NO), None)
            if anchor is None:
                raise SystemExit(f"找不到第 {CHAPTER_NO} 回对应的时间锚点")

            characters = await repo.get_characters_by_slugs(conn, work["id"], [CHARACTER_SLUG])
            if not characters:
                raise SystemExit("找不到林冲，请确认已导入 db/seed.sql")
            character = characters[0]

            user = await repo.ensure_user(conn, ACCOUNT, "开发账号")
            await repo.set_current_anchor(conn, user["id"], work["id"], anchor["id"])

            rows = []
            for spec in IDENTITIES:
                persona = await upsert_persona(conn, user["id"], work["id"], spec)
                existing = await conn.fetchval(
                    """
                    SELECT s.id FROM sessions s
                    JOIN session_members m
                      ON m.session_id = s.id AND m.member_kind = 'character' AND m.member_id = $2
                    WHERE s.persona_id = $1 AND s.session_type = 'direct' AND s.archived_at IS NULL
                    ORDER BY s.id LIMIT 1
                    """,
                    persona["id"],
                    character["id"],
                )
                session_id = existing or (
                    await repo.create_session(
                        conn,
                        persona_id=persona["id"],
                        work_id=work["id"],
                        session_type="direct",
                        title=f"与{character['name']}说话",
                        character_ids=[character["id"]],
                        created_anchor_id=anchor["id"],
                    )
                )["id"]
                rows.append((spec, persona, session_id))
    finally:
        await db.disconnect()

    print(f"账号：{ACCOUNT}（id {user['id']}）　时间线：{anchor['chapter_label']}《{anchor['name']}》")
    print()
    print(f"{'身份':<6}{'persona':<9}{'会话':<7}观察点")
    for spec, persona, session_id in rows:
        print(f"{spec['name']:<6}{persona['id']:<9}{session_id:<7}{spec['expect']}")
    print()
    print("打开试验台切换身份：http://127.0.0.1:8002/  （左上「我的角色」）")
    print("直接看 prompt：")
    for spec, _, session_id in rows:
        print(f"  curl -s 'localhost:8002/api/sessions/{session_id}/prompt-preview' | python3 -m json.tool")


if __name__ == "__main__":
    asyncio.run(main())
