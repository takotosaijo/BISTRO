"""F10：会话隔离。

产品的承诺是「每个会话互相隔离」：消息、上下文（人设 / 时间点）、生成的回复都不许串。
隔离的判据不是「接口看着对」，而是**送进模型的 message 列表里有没有别人的东西**——
所以这里直接调 `chat_service.prepare_turn` 看它装配出来的 messages。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List

from httpx import AsyncClient

from app import repository as repo
from app.services import chat as chat_service

WORK = "shuihu-100"


async def _new_user(client: AsyncClient, persona_name: str, chapter_no: int) -> Dict[str, Any]:
    response = await client.post(
        "/api/users",
        json={"external_id": f"iso-{uuid.uuid4().hex[:10]}", "display_name": persona_name},
    )
    assert response.status_code == 200, response.text
    user = response.json()

    response = await client.put(
        f"/api/users/{user['id']}/persona",
        json={
            "work_slug": WORK,
            "name": persona_name,
            "identity": "东京城里开酒铺的掌柜",
            "speech_style": "客气里带点精明",
        },
    )
    assert response.status_code == 200, response.text

    response = await client.put(
        f"/api/users/{user['id']}/timeline",
        json={"work_slug": WORK, "chapter_no": chapter_no},
    )
    assert response.status_code == 200, response.text
    return user


async def _new_session(
    client: AsyncClient,
    user_id: int,
    slugs: List[str],
    session_type: str = "direct",
) -> Dict[str, Any]:
    response = await client.post(
        "/api/sessions",
        json={
            "user_id": user_id,
            "work_slug": WORK,
            "session_type": session_type,
            "character_slugs": slugs,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _flatten(prepared: Any) -> str:
    return "\n".join(message.content for message in prepared.messages)


async def test_two_sessions_of_one_user_do_not_share_history(client, conn) -> None:
    """同一个用户开两个会话：各自的消息、seq、上下文都是自己的。"""

    user = await _new_user(client, "张三", 10)
    session_a = await _new_session(client, user["id"], ["lin-chong"])
    session_b = await _new_session(client, user["id"], ["lin-chong"])

    await chat_service.prepare_turn(conn, session_a["id"], "东边那间铺子是我的")
    await chat_service.prepare_turn(conn, session_b["id"], "西边那座桥是我修的")

    prepared_a = await chat_service.prepare_turn(conn, session_a["id"], "再问一句")
    prepared_b = await chat_service.prepare_turn(conn, session_b["id"], "也再问一句")

    text_a = _flatten(prepared_a)
    text_b = _flatten(prepared_b)
    assert "东边那间铺子是我的" in text_a
    assert "西边那座桥是我修的" not in text_a, "另一个会话的消息串进了会话 A"
    assert "西边那座桥是我修的" in text_b
    assert "东边那间铺子是我的" not in text_b, "另一个会话的消息串进了会话 B"

    # seq 是会话内的编号，各数各的
    rows_a = await conn.fetch(
        "SELECT session_id, seq FROM messages WHERE session_id = $1 ORDER BY seq",
        session_a["id"],
    )
    rows_b = await conn.fetch(
        "SELECT session_id, seq FROM messages WHERE session_id = $1 ORDER BY seq",
        session_b["id"],
    )
    assert [r["seq"] for r in rows_a] == [1, 2]
    assert [r["seq"] for r in rows_b] == [1, 2]
    assert {r["session_id"] for r in rows_a} == {session_a["id"]}
    assert {r["session_id"] for r in rows_b} == {session_b["id"]}


async def test_two_users_do_not_share_persona_or_history(client, conn) -> None:
    """两个用户跟同一个角色说话：谁的人设、谁的话都不能进对方的 prompt。"""

    zhang = await _new_user(client, "张三", 10)
    li = await _new_user(client, "李四", 10)
    session_a = await _new_session(client, zhang["id"], ["lin-chong"])
    session_b = await _new_session(client, li["id"], ["lin-chong"])

    await chat_service.prepare_turn(conn, session_a["id"], "我姓张，城东开酒铺")
    await chat_service.prepare_turn(conn, session_b["id"], "我姓李，城西贩布")

    prepared_a = await chat_service.prepare_turn(conn, session_a["id"], "教头近来可好")
    prepared_b = await chat_service.prepare_turn(conn, session_b["id"], "教头近来可好")

    assert "张三" in prepared_a.system_prompt
    assert "李四" not in prepared_a.system_prompt, "另一个用户的人设串进了会话 A"
    assert "李四" in prepared_b.system_prompt
    assert "张三" not in prepared_b.system_prompt, "另一个用户的人设串进了会话 B"
    assert "我姓李，城西贩布" not in _flatten(prepared_a)


async def test_parallel_turns_in_two_sessions_do_not_cross(client, conn) -> None:
    """两个会话同时发消息（并发），回复与落库都不能串。"""

    user = await _new_user(client, "张三", 10)
    lin = await _new_session(client, user["id"], ["lin-chong"])
    gao = await _new_session(client, user["id"], ["gao-qiu"])

    async def send(session_id: int, text: str) -> Dict[str, Any]:
        response = await client.post(
            f"/api/sessions/{session_id}/messages", json={"content": text}
        )
        assert response.status_code == 200, response.text
        return response.json()

    lin_reply, gao_reply = await asyncio.gather(
        send(lin["id"], "林教头，这雪夜往哪里去？"),
        send(gao["id"], "太尉近来可好？"),
    )

    # 各自按自己的角色与此刻处境作答（mock provider 会把处境回显出来）
    assert lin_reply["responder"]["slug"] == "lin-chong"
    assert "沧州往梁山途中" in lin_reply["reply"]["content"]
    assert "东京开封府" not in lin_reply["reply"]["content"]
    assert gao_reply["responder"]["slug"] == "gao-qiu"
    assert "东京开封府" in gao_reply["reply"]["content"]
    assert "沧州往梁山途中" not in gao_reply["reply"]["content"]

    # 落库：每个会话两条消息，角色消息的发话人必须是本会话的角色
    characters = await repo.get_work_by_slug(conn, WORK)
    ids = {
        c["slug"]: c["id"]
        for c in await repo.list_characters(conn, characters["id"])
    }
    for session, slug in ((lin, "lin-chong"), (gao, "gao-qiu")):
        rows = await conn.fetch(
            """
            SELECT sender_kind, sender_id FROM messages
            WHERE session_id = $1 ORDER BY seq
            """,
            session["id"],
        )
        assert [r["sender_kind"] for r in rows] == ["user", "character"]
        assert rows[1]["sender_id"] == ids[slug], f"会话 {session['id']} 的角色消息发话人不对"


async def test_pinned_session_keeps_its_anchor_while_timeline_moves(client, conn) -> None:
    """会话可以固定在某个时间点：用户整体拨动时间线，固定住的那个会话不受影响。"""

    user = await _new_user(client, "张三", 10)
    pinned = await _new_session(client, user["id"], ["lin-chong"])
    following = await _new_session(client, user["id"], ["lin-chong"])

    # 固定锚点在 schema 里是支持的，但接口还没开放，这里直接写库
    anchor_10 = await conn.fetchval(
        """
        SELECT a.id FROM timeline_anchors a
        JOIN works w ON w.id = a.work_id
        WHERE w.slug = $1 AND a.chapter_no = 10
        """,
        WORK,
    )
    await conn.execute(
        "UPDATE sessions SET pinned_anchor_id = $1 WHERE id = $2", anchor_10, pinned["id"]
    )

    response = await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": 71}
    )
    assert response.status_code == 200, response.text

    prepared_pinned = await chat_service.prepare_turn(conn, pinned["id"], "你在哪里")
    prepared_following = await chat_service.prepare_turn(conn, following["id"], "你在哪里")

    assert "第十回" in prepared_pinned.system_prompt
    assert "山神庙" in prepared_pinned.system_prompt
    assert "第七十一回" not in prepared_pinned.system_prompt
    assert "第七十一回" in prepared_following.system_prompt
    assert "山神庙" not in prepared_following.system_prompt
