"""I11：记忆按时间锚点分层——往前携带，往回忘掉未来。

判据是**送进模型的消息列表**而不是接口返回值：用户自己当然记得全部，
但角色的上下文里，只能出现「此刻及之前」说过的话。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from httpx import AsyncClient

from app.services import chat as chat_service

WORK = "shuihu-100"


async def _user(client: AsyncClient, chapter_no: int) -> Dict[str, Any]:
    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"anchor-{uuid.uuid4().hex[:8]}", "display_name": "张三"},
        )
    ).json()
    persona = await client.post(
        f"/api/users/{user['id']}/personas",
        json={"work_slug": WORK, "name": "张三", "identity": "东京城里开酒铺的掌柜"},
    )
    user["persona"] = persona.json()
    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": chapter_no}
    )
    return user


async def _session(client: AsyncClient, persona_id: int) -> Dict[str, Any]:
    return (
        await client.post(
            "/api/sessions",
            json={
                "persona_id": persona_id,
                "work_slug": WORK,
                "session_type": "direct",
                "character_slugs": ["lin-chong"],
            },
        )
    ).json()


def _text(prepared: Any) -> str:
    return "\n".join(message.content for message in prepared.messages)


async def test_past_messages_are_carried_forward(client, conn) -> None:
    """第 10 回说过的话，滑到第 71 回仍在他记得的范围内。"""

    user = await _user(client, 10)
    session = await _session(client, user["persona"]["id"])
    await chat_service.prepare_turn(conn, session["id"], "林教头，我姓张，城东开酒铺")

    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": 71}
    )
    prepared = await chat_service.prepare_turn(conn, session["id"], "教头还认得我么")

    assert "我姓张，城东开酒铺" in _text(prepared)
    assert "第七十一回" in prepared.system_prompt
    # 隔了些时日是真的，但不再说「中间的事你未必知晓」这种与上下文自相矛盾的话
    assert "时间已推移" in prepared.system_prompt
    assert "你未必知晓" not in prepared.system_prompt


async def test_future_messages_are_dropped_when_sliding_back(client, conn) -> None:
    """在第 71 回说过的话，滑回第 10 回就不该出现在上下文里。"""

    user = await _user(client, 10)
    session = await _session(client, user["persona"]["id"])
    await chat_service.prepare_turn(conn, session["id"], "第十回：我陪你走一程")

    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": 71}
    )
    await chat_service.prepare_turn(conn, session["id"], "第七十一回：我如今也上山了")

    # 往回拨
    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": 10}
    )
    prepared = await chat_service.prepare_turn(conn, session["id"], "教头，别来无恙")
    text = _text(prepared)

    assert "第十回：我陪你走一程" in text, "过去的话不该被忘掉"
    assert "第七十一回：我如今也上山了" not in text, "未来聊过的内容漏进了上下文"
    assert "第十回" in prepared.system_prompt
    assert "第七十一回" not in prepared.system_prompt


async def test_raw_log_still_returns_everything(client, conn) -> None:
    """接口原样返回整条日志——用户自己记得，被过滤的只是角色能看到的那部分。"""

    user = await _user(client, 10)
    session = await _session(client, user["persona"]["id"])
    await chat_service.prepare_turn(conn, session["id"], "第十回的话")
    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": 71}
    )
    await chat_service.prepare_turn(conn, session["id"], "第七十一回的话")
    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": 10}
    )

    body = (await client.get(f"/api/sessions/{session['id']}/messages")).json()
    contents = [m["content"] for m in body]
    assert "第十回的话" in contents and "第七十一回的话" in contents
