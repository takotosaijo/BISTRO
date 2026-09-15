"""第 0 期验收测试：时间线 → 状态 → 关系 → prompt → 生成 → 落库。"""

from __future__ import annotations

import json

from httpx import AsyncClient

from tests.conftest import WORK, make_user


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["llm_provider"] == "mock"


async def test_prompt_reflects_anchor_and_character_state(client: AsyncClient) -> None:
    """第十回的林冲应当是「刺配途中」，而不是梁山头领。"""

    _, session = await make_user(client, chapter_no=10, character_slugs=["lin-chong"])
    response = await client.get(f"/api/sessions/{session['id']}/prompt-preview")
    assert response.status_code == 200, response.text
    prompt = response.json()["system_prompt"]

    assert "你是《水浒传》中的林冲" in prompt
    assert "第十回" in prompt
    assert "沧州往梁山途中" in prompt
    assert "刺配的教头" in prompt
    assert "山神庙" in prompt
    assert "张三" in prompt
    # 知识边界必须写死
    assert "你只经历过第十回之前的事" in prompt
    # 底细只给恒定层，行为约束负责「别顺着用户的预告往下编」
    assert "你此刻一概不知" in prompt
    # 而未来信息本身就不该出现——不是靠提示压着，是压根没进 prompt（F13）
    assert "第六把交椅" not in prompt
    assert "五虎将" not in prompt
    assert "招安" not in prompt


async def test_timeline_advance_rewrites_prompt(client: AsyncClient) -> None:
    """同一个人、同一个会话，拨动时间线之后 prompt 应当整体换掉。"""

    user, session = await make_user(client, chapter_no=10, character_slugs=["lin-chong"])
    before = await client.get(f"/api/sessions/{session['id']}/prompt-preview")
    assert "山神庙" in before.json()["system_prompt"]

    response = await client.put(
        f"/api/users/{user['id']}/timeline",
        json={"work_slug": WORK, "chapter_no": 71},
    )
    assert response.status_code == 200, response.text

    after = await client.get(f"/api/sessions/{session['id']}/prompt-preview")
    prompt = after.json()["system_prompt"]
    assert "第七十一回" in prompt
    assert "第六把交椅" in prompt
    assert "校场演武" in prompt or "校场" in prompt
    assert "山神庙" not in prompt


async def test_relationship_is_directional_and_private(client: AsyncClient) -> None:
    """林冲恨高俅，高俅也在算计林冲——两边看到的必须是不同的东西。"""

    _, session = await make_user(
        client, chapter_no=10, character_slugs=["lin-chong", "gao-qiu"], session_type="group"
    )

    lin = await client.get(
        f"/api/sessions/{session['id']}/prompt-preview", params={"responder": "lin-chong"}
    )
    lin_prompt = lin.json()["system_prompt"]
    assert "对 高俅：不共戴天的死仇" in lin_prompt
    assert "此仇不报，枉为人" in lin_prompt

    gao = await client.get(
        f"/api/sessions/{session['id']}/prompt-preview", params={"responder": "gao-qiu"}
    )
    gao_prompt = gao.json()["system_prompt"]
    assert "对 林冲：眼中钉，必欲除之" in gao_prompt
    assert "留他一日，本官一日不安" in gao_prompt
    # 高俅这层心思是不外露的
    assert "这只是你心里的想法，对方并不知道。" in gao_prompt
    # 两边看到的是各自的内心话，不能串
    assert "此仇不报，枉为人" not in gao_prompt


async def test_chat_round_trip_persists_and_uses_state(
    client: AsyncClient, conn
) -> None:
    user, session = await make_user(client, chapter_no=10, character_slugs=["lin-chong"])

    response = await client.post(
        f"/api/sessions/{session['id']}/messages",
        json={"content": "林教头，这雪夜赶路，是要往哪里去？"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # 回复里带上了角色此刻的处境，说明时间线状态真的进了模型
    assert "山神庙" in body["reply"]["content"]
    assert body["anchor"]["chapter_label"] == "第十回"
    assert body["responder"]["slug"] == "lin-chong"

    # 两条消息都落库，seq 连续
    messages = (await client.get(f"/api/sessions/{session['id']}/messages")).json()
    assert [m["seq"] for m in messages] == [1, 2]
    assert messages[0]["sender_kind"] == "user"
    assert messages[1]["sender_kind"] == "character"
    assert messages[1]["sender_id"] is not None

    # 互动次数被记了下来
    row = await conn.fetchrow(
        "SELECT interaction_count FROM user_character_relations WHERE user_id = $1",
        user["id"],
    )
    assert row["interaction_count"] == 1


async def test_deceased_character_is_blocked_at_end_anchor(client: AsyncClient) -> None:
    """第一百回时林冲已亡故，默认不允许对话。"""

    _, session = await make_user(client, chapter_no=100, character_slugs=["lin-chong"])
    response = await client.post(
        f"/api/sessions/{session['id']}/messages", json={"content": "林教头，别来无恙？"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "character_unavailable"
    assert "已经不在人世" in response.json()["message"]


async def test_survivor_still_chattable_at_end_anchor(client: AsyncClient) -> None:
    """同一时间点，武松在六和寺出家，可以正常说话。"""

    _, session = await make_user(client, chapter_no=100, character_slugs=["wu-song"])
    response = await client.post(
        f"/api/sessions/{session['id']}/messages", json={"content": "武二哥，别来无恙？"}
    )
    assert response.status_code == 200, response.text
    assert "六和寺" in response.json()["reply"]["content"]


async def test_streaming_endpoint(client: AsyncClient) -> None:
    _, session = await make_user(client, chapter_no=10, character_slugs=["lin-chong"])

    events = []
    collected = ""
    async with client.stream(
        "POST",
        f"/api/sessions/{session['id']}/messages/stream",
        json={"content": "林教头，前面是什么去处？"},
    ) as response:
        assert response.status_code == 200
        current_event = None
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                events.append(current_event)
                if current_event == "delta":
                    collected += payload["text"]

    assert events[0] == "meta"
    assert "delta" in events
    assert events[-1] == "done"
    assert "山神庙" in collected

    messages = (await client.get(f"/api/sessions/{session['id']}/messages")).json()
    assert len(messages) == 2
    assert messages[1]["content"] == collected


async def test_character_list_marks_selectable(client: AsyncClient) -> None:
    user, _ = await make_user(client, chapter_no=2, character_slugs=["shi-jin"])
    response = await client.get(
        f"/api/works/{WORK}/characters", params={"user_id": user["id"]}
    )
    assert response.status_code == 200
    by_slug = {c["slug"]: c for c in response.json()}
    assert by_slug["shi-jin"]["state_at_anchor"]["availability"] == "introduced"
    assert by_slug["shi-jin"]["selectable"] is True
    assert by_slug["wu-song"]["state_at_anchor"]["availability"] == "not_introduced"
