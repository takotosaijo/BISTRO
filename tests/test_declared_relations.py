"""F12：人设语义 → 结构化关系。

保存身份时解析一次，落成**一对有向的声明边**（用户怎么看他 / 他怎么看她），
prompt 以它为准——不再对「我是他女儿」这种自述回一句「素不相识」。

测试走 mock 解析器（关键词规则 + 必须点名提到角色），保证离线可跑、结果确定；
真实模型的行为由 `make eval` 与人工抽查把关。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from httpx import AsyncClient

WORK = "shuihu-100"


async def _persona_with(
    client: AsyncClient, identity_text: str, name: str = "玉娆"
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"decl-{uuid.uuid4().hex[:8]}", "display_name": name},
        )
    ).json()
    persona = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={"work_slug": WORK, "name": name, "identity": identity_text},
        )
    ).json()
    return user, persona


async def _prompt_for(client: AsyncClient, persona_id: int, chapter_no: int = 10) -> str:
    session = (
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
    response = await client.get(f"/api/sessions/{session['id']}/prompt-preview")
    assert response.status_code == 200, response.text
    return response.json()["system_prompt"]


async def test_declared_relation_is_parsed_on_save(client: AsyncClient) -> None:
    _, persona = await _persona_with(client, "林冲失散多年的私生女，随母姓")

    assert persona["relations"], "保存身份时应当解析出与角色的关系"
    assert persona["relations"][0]["character"] == "lin-chong"
    assert persona["relations"][0]["character_knows"] is False


async def test_prompt_shows_both_directions_instead_of_stranger(client: AsyncClient) -> None:
    """用户写「我是他女儿」，prompt 里就不该再出现「素不相识」。"""

    _, persona = await _persona_with(client, "林冲失散多年的私生女，随母姓")
    prompt = await _prompt_for(client, persona["id"])

    assert "素不相识，初次照面" not in prompt
    assert "玉娆认定：" in prompt          # 用户那一侧：她认定他是爹
    assert "你心里：" in prompt            # 角色那一侧：他并不认得她


async def test_plain_persona_stays_a_stranger(client: AsyncClient) -> None:
    """自述里没有提到任何角色时，解析不出关系，保持素不相识。"""

    _, persona = await _persona_with(client, "东京城里开酒铺的掌柜，常与江湖人打交道", "张三")
    assert persona["relations"] == []

    prompt = await _prompt_for(client, persona["id"])
    assert "素不相识，初次照面" in prompt


async def test_old_acquaintance_is_known_from_the_start(client: AsyncClient) -> None:
    """「林冲东京时的旧相识」——角色那一侧一开始就该认得，而不是素不相识。"""

    _, persona = await _persona_with(
        client, "林冲在东京时的旧相识，当年未及提亲他就出了事", "苏娘"
    )
    prompt = await _prompt_for(client, persona["id"])

    assert "素不相识，初次照面" not in prompt
    assert "你心里：" in prompt


async def test_relations_are_scoped_to_the_persona(client: AsyncClient) -> None:
    """同一个账号下，两个身份各认各的关系，互不影响。"""

    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"decl-{uuid.uuid4().hex[:8]}", "display_name": "多身份"},
        )
    ).json()
    daughter = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={"work_slug": WORK, "name": "玉娆", "identity": "林冲失散多年的私生女"},
        )
    ).json()
    passerby = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={"work_slug": WORK, "name": "张三", "identity": "东京城里开酒铺的掌柜"},
        )
    ).json()

    assert daughter["relations"] and not passerby["relations"]
    assert "素不相识，初次照面" not in await _prompt_for(client, daughter["id"])
    assert "素不相识，初次照面" in await _prompt_for(client, passerby["id"])


async def test_updating_persona_reparses_relations(client: AsyncClient) -> None:
    """改自述再保存，声明跟着更新（用户手改的边由 is_manual 兜底，见 DECISIONS）。"""

    _, persona = await _persona_with(client, "东京城里开酒铺的掌柜", "张三")
    assert persona["relations"] == []

    updated = (
        await client.put(
            f"/api/personas/{persona['id']}",
            json={"name": "苏娘", "identity": "林冲在东京时的旧相识，当年未及提亲"},
        )
    ).json()
    assert updated["relations"], "改了自述之后应当重新解析出关系"

    prompt = await _prompt_for(client, persona["id"])
    assert "素不相识，初次照面" not in prompt


async def test_manual_relation_survives_reparse(client, conn) -> None:
    """用户手改过的声明（is_manual）不许被重新解析覆盖——这是 DECISIONS 里的约定。"""

    from app import repository as repo

    _, persona = await _persona_with(client, "林冲失散多年的私生女")
    character = (
        await client.get(f"/api/works/{WORK}/characters")
    ).json()
    lin_chong = next(c for c in character if c["slug"] == "lin-chong")

    await conn.execute(
        """
        UPDATE relationship_edges SET is_manual = true, label = '我自己改过的说法'
        WHERE source = 'user' AND persona_id = $1 AND from_kind = 'user' AND to_id = $2
        """,
        persona["id"],
        lin_chong["id"],
    )
    # 再保存一次身份（会触发重新解析），手改的那条必须原样保留
    await client.put(
        f"/api/personas/{persona['id']}",
        json={"name": "玉娆", "identity": "林冲失散多年的私生女，随母姓"},
    )
    labels = await conn.fetch(
        """
        SELECT label FROM relationship_edges
        WHERE source = 'user' AND persona_id = $1 AND from_kind = 'user' AND to_id = $2
        """,
        persona["id"],
        lin_chong["id"],
    )
    assert [row["label"] for row in labels] == ["我自己改过的说法"]
