"""F14：关系演化写成新的边版本，并且按锚点可回退。

场景：用户自称是林冲失散多年的女儿，聊到相认；然后
  - 往后滑（第 71 回）：仍然认下
  - 往回滑（第二回，相认之前）：回到「并不认得这个年轻人」
测试走 mock 变化检测器（离线规则），真实模型的行为由人工抽查与 `make eval` 把关。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from httpx import AsyncClient

WORK = "shuihu-100"


async def _setup(client: AsyncClient, chapter_no: int = 10) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"evolve-{uuid.uuid4().hex[:8]}", "display_name": "玉娆"},
        )
    ).json()
    persona = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={"work_slug": WORK, "name": "玉娆", "identity": "林冲失散多年的私生女，随母姓"},
        )
    ).json()
    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": chapter_no}
    )
    session = (
        await client.post(
            "/api/sessions",
            json={
                "persona_id": persona["id"],
                "work_slug": WORK,
                "session_type": "direct",
                "character_slugs": ["lin-chong"],
            },
        )
    ).json()
    return user, {"persona": persona, "session": session, "user": user}


async def _prompt(client: AsyncClient, session_id: int) -> str:
    response = await client.get(f"/api/sessions/{session_id}/prompt-preview")
    assert response.status_code == 200, response.text
    return response.json()["system_prompt"]


async def _move(client: AsyncClient, user_id: int, chapter_no: int) -> None:
    response = await client.put(
        f"/api/users/{user_id}/timeline", json={"work_slug": WORK, "chapter_no": chapter_no}
    )
    assert response.status_code == 200, response.text


async def test_recognition_becomes_a_new_edge_version(client, conn) -> None:
    """相认之后，「你心里」那半句要变，并且留下审计。"""

    user, ctx = await _setup(client)
    before = await _prompt(client, ctx["session"]["id"])
    assert "并不认得这个年轻人" in before

    response = await client.post(
        f"/api/sessions/{ctx['session']['id']}/messages",
        json={"content": "我是你失散多年的女儿，你认了我吧"},
    )
    assert response.status_code == 200, response.text
    changes = response.json()["relation_changes"]
    assert changes, "这一轮该被判定为发生了关系变化"
    # 结构化判定：F14 的验针认 kind，不认 label 的措辞（抠字眼会把
    # 「却仍不肯当面认下这个女儿」当成认下，2026-09-16 踩过）
    assert changes[0]["kind"] == "recognition"

    after = await _prompt(client, ctx["session"]["id"])
    assert "你已认下这门亲" in after
    assert "并不认得这个年轻人" not in after

    audit = await conn.fetch(
        """
        SELECT direction, label_before, label_after, anchor_id, reason
        FROM relationship_changes WHERE persona_id = $1 ORDER BY id DESC
        """,
        ctx["persona"]["id"],
    )
    assert audit, "关系变化要留下审计"
    assert audit[0]["direction"] == "character_to_user"
    assert audit[0]["label_after"] == "你已认下这门亲"


async def test_recognition_is_carried_forward(client, conn) -> None:
    """相认发生在第十回：滑到第七十一回仍然认下。"""

    user, ctx = await _setup(client)
    await client.post(
        f"/api/sessions/{ctx['session']['id']}/messages",
        json={"content": "我是你失散多年的女儿，你认了我吧"},
    )
    await _move(client, user["id"], 71)

    prompt = await _prompt(client, ctx["session"]["id"])
    assert "你已认下这门亲" in prompt


async def test_sliding_back_before_recognition_restores_the_old_label(client, conn) -> None:
    """往回滑到相认之前：旧说法自动重新生效，不是特判出来的。"""

    user, ctx = await _setup(client)
    await client.post(
        f"/api/sessions/{ctx['session']['id']}/messages",
        json={"content": "我是你失散多年的女儿，你认了我吧"},
    )
    await _move(client, user["id"], 2)

    prompt = await _prompt(client, ctx["session"]["id"])
    assert "并不认得这个年轻人" in prompt
    assert "你已认下这门亲" not in prompt


async def test_plain_chat_does_not_change_relations(client, conn) -> None:
    """没发生关系变化的对话，不许写边、也不许写审计。"""

    user, ctx = await _setup(client)
    response = await client.post(
        f"/api/sessions/{ctx['session']['id']}/messages",
        json={"content": "教头，这雪夜往哪里去？"},
    )
    assert response.json()["relation_changes"] == []

    count = await conn.fetchval(
        "SELECT count(*) FROM relationship_changes WHERE persona_id = $1", ctx["persona"]["id"]
    )
    assert count == 0
