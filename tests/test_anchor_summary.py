"""F22：会话摘要按时间锚点分段——每章一条，prompt 只取「窗口之外」的前情。

判据是**落库的行**与**送进模型的 prompt**，不看接口返回值：
  1. 一章攒够 `summary_every` 条消息才压一次（摘要是一次模型调用，不是免费的）
  2. 每章一条：第十回与第七十一回各一条，互不覆盖
  3. 原话还在最近窗口里时，不给摘要——同一段事说两遍只会让角色绕圈子
  4. 原话被窗口甩掉之后，摘要补上（这就是「替代只取最近 24 条原文」）
  5. 未来的提要不许漏进当前时间点的 prompt（与 I11 同一条规矩）
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from httpx import AsyncClient

from app import repository as repo
from app.config import settings

WORK = "shuihu-100"


async def _setup(client: AsyncClient, chapter_no: int = 10) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"summary-{uuid.uuid4().hex[:8]}", "display_name": "张三"},
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


async def _say(client: AsyncClient, session_id: int, text: str = "教头，这雪夜往哪里去？") -> None:
    response = await client.post(f"/api/sessions/{session_id}/messages", json={"content": text})
    assert response.status_code == 200, response.text


async def _prompt(client: AsyncClient, session_id: int) -> str:
    response = await client.get(f"/api/sessions/{session_id}/prompt-preview")
    assert response.status_code == 200, response.text
    return response.json()["system_prompt"]


async def _summaries(conn, session_id: int) -> list:
    return await conn.fetch(
        "SELECT * FROM session_summaries WHERE session_id = $1 ORDER BY anchor_id",
        session_id,
    )


async def test_anchor_summary_waits_until_there_is_enough_to_compress(client, conn) -> None:
    """攒够 6 条才压一次——每轮都压等于每轮多花一次模型调用。"""

    _, session = await _setup(client)
    for _ in range(2):  # 4 条消息
        await _say(client, session["id"])
    assert await _summaries(conn, session["id"]) == [], "还没攒够就不该压"

    await _say(client, session["id"])  # 6 条
    rows = await _summaries(conn, session["id"])
    assert len(rows) == 1
    assert rows[0]["covered_from_seq"] == 1
    assert rows[0]["covered_to_seq"] == 6


async def test_anchor_summary_refreshes_only_every_few_messages(client, conn) -> None:
    """滚动刷新：每 6 条一版，且覆盖下界不许往后挪（挪了窗口重叠判断就会算错）。"""

    _, session = await _setup(client)
    for _ in range(3):
        await _say(client, session["id"])
    first = (await _summaries(conn, session["id"]))[0]

    await _say(client, session["id"])  # 8 条：还没到下一个 6
    same = (await _summaries(conn, session["id"]))[0]
    assert same["covered_to_seq"] == first["covered_to_seq"] == 6

    for _ in range(2):  # 12 条
        await _say(client, session["id"])
    third = (await _summaries(conn, session["id"]))[0]
    assert third["covered_to_seq"] == 12
    assert third["covered_from_seq"] == 1
    assert third["id"] == first["id"], "每章一条，是更新不是新增"


async def test_anchor_summary_hides_while_the_words_are_still_in_the_window(client, conn) -> None:
    """原话还全在窗口里时不给提要——同一段事说两遍，角色会绕圈子。"""

    _, session = await _setup(client)
    for _ in range(3):
        await _say(client, session["id"])

    assert await _summaries(conn, session["id"]), "摘要该写了"
    prompt = await _prompt(client, session["id"])
    assert "离线提要" not in prompt, "6 条消息全在 24 条的窗口里，不该再摘要一遍"


async def test_anchor_summary_reaches_the_prompt_after_the_window_scrolls_past(
    client, conn, monkeypatch
) -> None:
    """窗口滚过去之后，摘要补上位置——这就是「替代只取最近 N 条原文」。"""

    monkeypatch.setattr(settings, "max_history_messages", 2)
    _, session = await _setup(client)
    for _ in range(3):
        await _say(client, session["id"])

    prompt = await _prompt(client, session["id"])
    assert "# 你和这个人更早说过的话" in prompt
    assert "离线提要" in prompt
    assert "第十回" in prompt


async def test_anchor_summary_is_per_anchor(client, conn) -> None:
    """每章一条：拨到下一章再聊，旧的那条原样留着，新的另起一条。"""

    user, session = await _setup(client)
    for _ in range(3):
        await _say(client, session["id"])
    first = (await _summaries(conn, session["id"]))[0]

    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": 71}
    )
    for _ in range(3):
        await _say(client, session["id"], "哥哥近来可好？")

    rows = await _summaries(conn, session["id"])
    assert len(rows) == 2, "第十回与第七十一回各一条"
    assert rows[0]["id"] == first["id"]
    assert rows[0]["covered_to_seq"] == first["covered_to_seq"], "旧章节的摘要不许被新章节改写"
    assert rows[1]["anchor_id"] != rows[0]["anchor_id"]


async def test_future_anchor_summary_does_not_leak_into_an_earlier_prompt(client, conn) -> None:
    """未来的提要不许出现在当前时间点的 prompt 里（I11 的规矩同样管摘要）。"""

    _, session = await _setup(client)
    work_id = await conn.fetchval("SELECT id FROM works WHERE slug = $1", WORK)
    future_anchor = await conn.fetchval(
        "SELECT id FROM timeline_anchors WHERE work_id = $1 AND seq = 10", work_id
    )
    await repo.upsert_anchor_summary(
        conn,
        session_id=session["id"],
        anchor_id=future_anchor,
        covered_from_seq=1,
        covered_to_seq=99,
        summary="他已经在梁山坐了第六把交椅，与宋江结义。",
    )

    prompt = await _prompt(client, session["id"])
    assert "第六把交椅" not in prompt
