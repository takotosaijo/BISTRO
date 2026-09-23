"""F23：四维度覆盖（用户 × 角色 × 锚点 × 会话）+ 来源标注。

规则（DECISIONS 2026-09-23）：同一格（key）命中多条时，**越具体越优先**——
会话 > 锚点 > 角色 > 用户；维度列留空表示不限，填了就必须与上下文相等才生效。
渲染进 prompt 时要标出「这条来自哪一层」，否则「他为什么这么说」查不回去。

测试里每条覆盖都挂在**本次测试自己的身份或会话**上：挂在作品层或角色层会污染别的用例的 prompt。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from httpx import AsyncClient

WORK = "shuihu-100"


async def _setup(client: AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"override-{uuid.uuid4().hex[:8]}", "display_name": "张三"},
        )
    ).json()
    persona = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={"work_slug": WORK, "name": "张三", "identity": "东京城里开酒铺的掌柜"},
        )
    ).json()
    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": 10}
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
    return user, persona, session


async def _override(client: AsyncClient, **fields) -> Dict[str, Any]:
    response = await client.post(
        "/api/prompt-overrides", json={"work_slug": WORK, **fields}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _prompt(client: AsyncClient, session_id: int) -> str:
    response = await client.get(f"/api/sessions/{session_id}/prompt-preview")
    assert response.status_code == 200, response.text
    return response.json()["system_prompt"]


async def test_prompt_override_reaches_the_prompt_with_its_source(client, conn) -> None:
    """写一条用户层的覆盖，prompt 里要出现它，并且标出来自「用户」这一层。"""

    _, persona, session = await _setup(client)
    await _override(
        client,
        key="说话分寸",
        body="他今天刚死了兄弟，话要短，别开玩笑。",
        persona_id=persona["id"],
        note="试验台手测时发现他太贫嘴",
    )

    prompt = await _prompt(client, session["id"])
    assert "# 本轮特别交代" in prompt
    assert "[用户] 说话分寸：他今天刚死了兄弟，话要短，别开玩笑。" in prompt

    await conn.execute("DELETE FROM prompt_overrides WHERE persona_id = $1", persona["id"])


async def test_prompt_override_more_specific_scope_wins(client, conn) -> None:
    """同一个 key：会话层压过用户层——越具体越优先，且来源标注跟着变。"""

    _, persona, session = await _setup(client)
    await _override(client, key="说话分寸", body="用户层：话要短。", persona_id=persona["id"])
    await _override(client, key="说话分寸", body="会话层：这一场别说话，只点头。", session_id=session["id"])

    effective = await client.get(f"/api/sessions/{session['id']}/prompt-overrides")
    assert effective.status_code == 200, effective.text
    rows = effective.json()
    assert len(rows) == 1, "同一个 key 只该留最具体的一条"
    assert rows[0]["scope"] == "会话"
    assert rows[0]["body"].startswith("会话层")

    prompt = await _prompt(client, session["id"])
    assert "[会话] 说话分寸：会话层" in prompt
    assert "用户层：话要短。" not in prompt, "被压下去的那条不该还在 prompt 里"

    await conn.execute("DELETE FROM prompt_overrides WHERE persona_id = $1", persona["id"])
    await conn.execute("DELETE FROM prompt_overrides WHERE session_id = $1", session["id"])


async def test_prompt_override_stays_inside_its_scope(client, conn) -> None:
    """挂在别的身份上的覆盖，不许出现在我的 prompt 里。"""

    _, persona, session = await _setup(client)
    other_user = (
        await client.post(
            "/api/users",
            json={"external_id": f"other-{uuid.uuid4().hex[:8]}", "display_name": "别人"},
        )
    ).json()
    other_persona = (
        await client.post(
            f"/api/users/{other_user['id']}/personas",
            json={"work_slug": WORK, "name": "别人", "identity": "路过的客人"},
        )
    ).json()
    await _override(
        client, key="说话分寸", body="这条只属于别人。", persona_id=other_persona["id"]
    )

    prompt = await _prompt(client, session["id"])
    assert "这条只属于别人。" not in prompt
    assert "# 本轮特别交代" not in prompt, "没有命中任何覆盖时不该留一个空标题"

    await conn.execute("DELETE FROM prompt_overrides WHERE persona_id = ANY($1::bigint[])",
                       [persona["id"], other_persona["id"]])
