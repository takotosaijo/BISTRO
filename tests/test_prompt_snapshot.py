"""F23：prompt 的版本与留痕。

要回答两个问题（DECISIONS 2026-09-16）：
  ① 这一章的 prompt 现在长什么样 → 靠 `prompt_inputs`（锚点输入）+ 卡片/模板版本
  ② 某一句回复当时送进去的到底是什么 → 靠 `prompt_snapshots`（真实快照）

这里钉住的是第 ② 条，以及第 ① 条的**原料**有没有记全：
  1. 每落一条回复，就有对应的一份快照（system + 对话 + 模型 + 模板版本）
  2. 快照是**当时**的：下一轮说过的话不许出现在上一轮的快照里
  3. 锚点输入按章记：关系快照 / 用到的摘要 / 卡片版本 / 模板版本
  4. 卡片一改，版本号 +1，老版本的内容留在 `character_card_versions` 里
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from httpx import AsyncClient

from app import repository as repo
from app.prompt import PROMPT_VERSION

WORK = "shuihu-100"


async def _setup(client: AsyncClient, chapter_no: int = 10) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"snap-{uuid.uuid4().hex[:8]}", "display_name": "张三"},
        )
    ).json()
    persona = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={"work_slug": WORK, "name": "张三", "identity": "东京城里开酒铺的掌柜"},
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
    return user, session


async def _say(client: AsyncClient, session_id: int, text: str) -> Dict[str, Any]:
    response = await client.post(f"/api/sessions/{session_id}/messages", json={"content": text})
    assert response.status_code == 200, response.text
    return response.json()


async def test_prompt_snapshot_records_what_went_to_the_model(client, conn) -> None:
    """每落一条回复，就有一份「当时送进去的」快照。"""

    _, session = await _setup(client)
    body = await _say(client, session["id"], "教头，这雪夜往哪里去？")
    reply_id = body["reply"]["id"]

    snapshot = await client.get(
        f"/api/sessions/{session['id']}/prompt-snapshot", params={"message_id": reply_id}
    )
    assert snapshot.status_code == 200, snapshot.text
    data = snapshot.json()

    assert data["message_id"] == reply_id
    assert data["provider"] == "mock" and data["model"]
    assert data["template_version"] == PROMPT_VERSION
    assert "林冲" in data["system_prompt"], "system prompt 不该是空的"
    assert any(
        message["role"] == "user" and "这雪夜往哪里去" in message["content"]
        for message in data["messages"]
    ), "这一轮用户说的话必须出现在快照的对话里"

    row = await conn.fetchrow(
        "SELECT provider, model, template_version FROM prompt_snapshots WHERE message_id = $1",
        reply_id,
    )
    assert row is not None, "快照要落库，不能只存在内存里"
    assert row["template_version"] == PROMPT_VERSION


async def test_prompt_snapshot_is_frozen_at_that_turn(client, conn) -> None:
    """快照是当时的：后面说过的话不许出现在前面那一轮的快照里。"""

    _, session = await _setup(client)
    first = await _say(client, session["id"], "教头，这雪夜往哪里去？")
    await _say(client, session["id"], "梁山泊可还远么？")

    snapshot = (
        await client.get(
            f"/api/sessions/{session['id']}/prompt-snapshot",
            params={"message_id": first["reply"]["id"]},
        )
    ).json()
    contents = [message["content"] for message in snapshot["messages"]]
    assert any("这雪夜往哪里去" in text for text in contents)
    assert not any("梁山泊可还远" in text for text in contents), (
        "第二轮的话漏进了第一轮的快照——那就不叫「当时送进去的」了"
    )


async def test_prompt_input_is_recorded_per_anchor(client, conn) -> None:
    """锚点输入：关系快照 / 用到的摘要 / 卡片版本 / 模板版本，按章各存一份。"""

    user, session = await _setup(client)
    await _say(client, session["id"], "教头，这雪夜往哪里去？")

    response = await client.get(f"/api/sessions/{session['id']}/prompt-input")
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["anchor"]["seq"] == 3, "第十回是锚点 seq 3"
    assert record["template_version"] == PROMPT_VERSION
    assert isinstance(record["summary_ids"], list)
    assert record["card_versions"], "至少要有这个角色的卡片版本"
    lin_chong_id = await conn.fetchval("SELECT id FROM characters WHERE slug = 'lin-chong'")
    assert str(lin_chong_id) in record["card_versions"]

    # 拨到下一章再说一句：另起一份锚点输入，旧的还在
    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": 71}
    )
    await _say(client, session["id"], "哥哥近来可好？")
    second = (await client.get(f"/api/sessions/{session['id']}/prompt-input")).json()
    assert second["anchor"]["seq"] == 10
    assert second["id"] != record["id"]

    count = await conn.fetchval(
        "SELECT count(*) FROM prompt_inputs WHERE session_id = $1", session["id"]
    )
    assert count == 2, "每章一份，不许互相覆盖"


async def test_character_card_version_bumps_when_the_card_changes(client, conn) -> None:
    """卡片一改就加一版，老版本的内容留在 character_card_versions 里。

    整个用例跑在一个会回滚的事务里——改的可是林冲的卡，别污染别的测试。
    """

    class _Rollback(Exception):
        pass

    character_id = await conn.fetchval("SELECT id FROM characters WHERE slug = 'lin-chong'")
    try:
        async with conn.transaction():
            before = await repo.ensure_character_card_version(conn, character_id)
            assert before is not None
            again = await repo.ensure_character_card_version(conn, character_id)
            assert again == before, "卡没改就不该加版本——否则每轮都会冒一个新版本"

            await conn.execute(
                "UPDATE characters SET identity = identity || '（测试改动）' WHERE id = $1",
                character_id,
            )
            after = await repo.ensure_character_card_version(conn, character_id)
            assert after == before + 1

            rows = await conn.fetch(
                """
                SELECT version, card FROM character_card_versions
                WHERE character_id = $1 ORDER BY version
                """,
                character_id,
            )
            assert rows[-1]["version"] == after
            assert "（测试改动）" in rows[-1]["card"]["identity"]
            assert rows[-2]["version"] == before
            assert "（测试改动）" not in rows[-2]["card"]["identity"], "老版本要留原样"

            current = await conn.fetchval(
                "SELECT card_version FROM characters WHERE id = $1", character_id
            )
            assert current == after
            raise _Rollback
    except _Rollback:
        pass
