"""F27：人设的其余字段要真的进 prompt（以前存了没人用）。

`appearance / speech_style / background / free_note` 四个字段一直是「存得下、用不上」：
只有 `identity` 进了 prompt。但角色看人不可能只看一句身份——长相、说话方式、
对方自己讲的来历，都是他开口前该知道的事。

两条规矩：
  1. 写了的字段要出现在 prompt 里（不然用户白填）
  2. 没写的字段不要留空壳标题（prompt 里多一行「他的模样：」只会让模型以为这人没模样）
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from httpx import AsyncClient

WORK = "shuihu-100"


async def _persona(client: AsyncClient, fields: Dict[str, Any]) -> Dict[str, Any]:
    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"persona-{uuid.uuid4().hex[:8]}", "display_name": "苏娘"},
        )
    ).json()
    persona = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={"work_slug": WORK, "name": "苏娘", **fields},
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
    return {"user": user, "persona": persona, "session": session}


async def _prompt(client: AsyncClient, session_id: int) -> str:
    response = await client.get(f"/api/sessions/{session_id}/prompt-preview")
    assert response.status_code == 200, response.text
    return response.json()["system_prompt"]


async def test_persona_fields_reach_the_prompt(client: AsyncClient) -> None:
    """四个字段写进身份，就得出现在送进模型的 prompt 里。"""

    ctx = await _persona(
        client,
        {
            "identity": "东京城里的旧相识，父亲是开绸缎庄的",
            "background": "当年与他在东京相国寺外结识，后来他遭了事，两家便断了音信",
            "appearance": "穿一身洗得发白的青布衣裳，鬓边别着一根旧银簪",
            "speech_style": "话少，说急了会带一点东京口音",
            "free_note": "她左手腕上有一道旧烫伤，是小时候替你挡滚水留下的",
        },
    )
    prompt = await _prompt(client, ctx["session"]["id"])

    assert "穿一身洗得发白的青布衣裳" in prompt, "外貌没进 prompt"
    assert "说急了会带一点东京口音" in prompt, "说话方式没进 prompt"
    assert "相国寺外结识" in prompt, "背景没进 prompt"
    assert "左手腕上有一道旧烫伤" in prompt, "free_note 没进 prompt"
    assert "（这些是他自己说的，信几分由你自己掂量。）" in prompt, (
        "人设是「对方自己说的」，不是全知事实——这句得留着，别让角色拿它当判词"
    )


async def test_persona_fields_blank_stays_quiet(client: AsyncClient) -> None:
    """没填的字段不留空壳：只写名字与身份时，不该冒出「他的模样：」这种空行。"""

    ctx = await _persona(client, {"identity": "东京城里开酒铺的掌柜"})
    prompt = await _prompt(client, ctx["session"]["id"])

    for label in ("他的模样", "他说话的样子", "他自己讲过的来历", "他特意交代过你的一件事"):
        assert label not in prompt, f"没填的字段不该出现「{label}」"
