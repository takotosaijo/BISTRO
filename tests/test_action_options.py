"""F26 情境选项：角色明确提出要求时给 3~4 个行动选项，外加自定义动作。

规格见 DECISIONS 2026-09-16「情境选项」。判定层离线用 mock 规则，真实模型的行为由
`make eval-relation` 与试验台手测把关；但**落库与「动作进历史」是确定性的，必须机械可判**：
  1. 角色提要求 → 3~4 个选项，挂在那条回复的 meta 上（零 schema 改动）
  2. 寻常说话 → 不给选项，但同样要写 meta（前端靠「键出现了」判断后台那次判定跑完）
  3. 数量由产品定死：给多了截断到 4，给不够 3 个就当没找准要求——宁可不给，
     也不给一个「只有一条路」的假选择
  4. 点选项 / 自己写动作 → 作为 message_kind='narration' 进历史，并且**真的进模型上下文**
     （带「（动作）」前缀，不然模型会把动作当台词读）
  5. 2026-09-23 补的两条硬闸：
     · 角色这轮什么都没说（「……」）→ 不给——现场就是空回复那轮写出了角色口吻的选项
     · 选项写成角色口吻（voice != user）→ 整组丢——用户点了会替角色说话
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from httpx import AsyncClient

from app import repository as repo
from app.services import chat as chat_service
from app.services.relation_evolve import (
    ActionOption,
    MockChangeDetector,
    TurnAnalysis,
    evolve_relations,
    parse_turn_analysis,
)

WORK = "shuihu-100"

# mock provider 会把用户的话原样回显，所以「角色提出要求」在离线测试里就是
# 「用户这轮被这样回了一句」——借它构造确定性输入（判定词见 MockChangeDetector.REQUEST）。
REQUEST_ECHO = "（他伸出手）玉佩拿来我看。"
PLAIN = "教头，这雪夜往哪里去？"


async def _setup(client: AsyncClient, chapter_no: int = 10) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"option-{uuid.uuid4().hex[:8]}", "display_name": "张三"},
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


async def _last_message(client: AsyncClient, session_id: int) -> Dict[str, Any]:
    messages = await client.get(f"/api/sessions/{session_id}/messages?limit=1")
    assert messages.status_code == 200, messages.text
    return messages.json()[-1]


async def test_action_options_only_when_character_asks() -> None:
    """判定层：他提要求才给选项，寻常寒暄不给。"""

    detector = MockChangeDetector()
    asked = await detector.detect(
        character_name="林冲",
        persona_summary="张三，酒铺掌柜",
        current_labels="（还没有声明过关系）",
        user_text="教头，你要怎样才肯信我？",
        reply_text="玉佩拿来我看。",
    )
    assert 3 <= len(asked.options) <= 4, "角色提要求时要给 3~4 个选项"
    assert all(option.label and option.action for option in asked.options)
    assert len({option.label for option in asked.options}) == len(asked.options), "选项不该重样"

    quiet = await detector.detect(
        character_name="林冲",
        persona_summary="张三，酒铺掌柜",
        current_labels="（还没有声明过关系）",
        user_text="教头，这雪夜往哪里去？",
        reply_text="林某往哪里去，与足下何干？",
    )
    assert quiet.options == [], "寻常说话不许弹选项——每轮都给会把对话变成点选游戏"


async def test_action_options_not_offered_for_a_plain_question() -> None:
    """问话不算要求：他问「你是谁」「几时没的」这种，打字就能答，不该弹选项。

    这是 2026-09-23 那次的根因——原来的触发条件把「要你说清楚某件事」也算进来，
    而角色几乎每轮都在问点什么，于是选项每轮都弹（27 个弹过的会话里 24 个是每轮都弹）。
    """

    detector = MockChangeDetector()
    asked = await detector.detect(
        character_name="宋江",
        persona_summary="苏娘，林冲在东京时的旧相识",
        current_labels="（还没有声明过关系）",
        user_text="我就是当年在东京和他相识的人",
        reply_text="娘子且慢，小可连娘子的来历都还不晓得。你且说说，你是何人，与林教头是何干系？",
    )
    assert asked.options == [], "问话不是要求——打字就能答，不该给行动选项"


class _StubDetector:
    """直接给出一组选项，用来单独验「写进 meta 之前的那两道硬闸」。"""

    def __init__(self, options) -> None:
        self.options = options

    async def detect(self, **kwargs) -> TurnAnalysis:
        return TurnAnalysis(changes=[], options=self.options)


async def _run_stub(client, conn, session_id: int, *, reply_text: str, options) -> dict:
    """借一条已经落库的回复，跑一次带桩判定器的 evolve_relations。"""

    body = (
        await client.post(
            f"/api/sessions/{session_id}/messages", json={"content": "教头，听我说一句。"}
        )
    ).json()
    reply_id = body["reply"]["id"]

    session = await repo.get_session(conn, session_id)
    anchor = await repo.get_current_anchor(conn, session["user_id"], session["work_id"])
    members = await repo.list_session_members(conn, session_id)
    characters = await repo.get_characters_by_ids(
        conn, [m["member_id"] for m in members if m["member_kind"] == "character"]
    )
    outcome = await evolve_relations(
        conn,
        session=session,
        responder=characters[0],
        anchor=anchor,
        user_text="教头，听我说一句。",
        reply_text=reply_text,
        message_id=reply_id,
        detector=_StubDetector(options),
    )
    stored = await conn.fetchrow(
        "SELECT meta FROM messages WHERE id = $1", reply_id
    )
    outcome["stored_meta"] = stored["meta"]
    return outcome


def _user_voiced() -> list:
    return [
        ActionOption(label="把玉佩递给他", action="我把那半块玉佩双手递到他面前。"),
        ActionOption(label="说玉佩丢了", action="我说那半块玉早在逃难路上丢了。"),
        ActionOption(label="反问一句", action="我反问他凭什么要这块玉。"),
    ]


async def test_action_options_dropped_when_the_character_says_nothing(client, conn) -> None:
    """角色这一轮一个字都没说（空回复占位）→ 没有话可接，宁可什么都不给。"""

    _, session = await _setup(client)
    outcome = await _run_stub(
        client, conn, session["id"], reply_text="……", options=_user_voiced()
    )
    assert outcome["action_options"] == []
    assert outcome["stored_meta"]["action_options_dropped"] == "empty_reply"


async def test_action_options_dropped_when_written_in_the_characters_voice(client, conn) -> None:
    """选项写成角色口吻 → 整组丢。用户点了它就成了替角色说话，角色下一轮必然一头雾水。"""

    _, session = await _setup(client)
    options = [
        ActionOption(
            label="如实相告",
            action="我压低声音对她说：林教头人还在，娘子不必过于忧心。",
            voice="character",
        ),
        ActionOption(label="推说不知", action="我摇头说我不晓得。", voice="character"),
        ActionOption(label="先问来历", action="我反问她可有凭据。", voice="character"),
    ]
    outcome = await _run_stub(
        client, conn, session["id"], reply_text="姑娘，你且起来说话。", options=options
    )
    assert outcome["action_options"] == []
    assert outcome["stored_meta"]["action_options_dropped"] == "character_voice"


def test_action_options_count_is_pinned_by_the_product() -> None:
    """数量由产品定死 3~4，不让模型自由发挥。"""

    def payload(count: int) -> str:
        options = ",".join(
            '{"label":"路%d","action":"我走第%d条路。"}' % (i, i) for i in range(count)
        )
        return '{"changes":[],"options":[%s]}' % options

    assert len(parse_turn_analysis(payload(5)).options) == 4, "给多了截断到 4"
    assert len(parse_turn_analysis(payload(4)).options) == 4
    assert len(parse_turn_analysis(payload(3)).options) == 3
    assert parse_turn_analysis(payload(2)).options == [], "给不够 3 个就当没找准要求"
    assert parse_turn_analysis(payload(0)).options == []


async def test_action_options_are_stored_on_the_reply_message(client: AsyncClient) -> None:
    """选项要落库才能被前端取回——挂在回复那条消息的 meta 上（零 schema 改动）。"""

    _, session = await _setup(client)
    response = await client.post(
        f"/api/sessions/{session['id']}/messages", json={"content": REQUEST_ECHO}
    )
    assert response.status_code == 200, response.text
    options = response.json()["action_options"]
    assert 3 <= len(options) <= 4, options

    stored = (await _last_message(client, session["id"]))["meta"]["action_options"]
    assert stored == options, "接口返回的选项与落库的必须一致"
    assert stored[0]["label"] and stored[0]["action"]


async def test_action_options_key_is_written_even_when_there_are_none(client: AsyncClient) -> None:
    """没有选项也要写这个键：前端靠「键出现了」判断后台判定跑完，不然只能干等。"""

    _, session = await _setup(client)
    response = await client.post(
        f"/api/sessions/{session['id']}/messages", json={"content": PLAIN}
    )
    assert response.status_code == 200, response.text
    assert response.json()["action_options"] == []

    meta = (await _last_message(client, session["id"]))["meta"]
    assert "action_options" in meta, "判定跑完要留痕，哪怕是空数组"
    assert meta["action_options"] == []


async def test_custom_action_option_reaches_the_model_as_narration(
    client: AsyncClient, conn
) -> None:
    """自己写的动作走 narration 进历史，并且真的进模型上下文（角色以后要记得）。"""

    _, session = await _setup(client)
    action = "我把那半块玉佩甩到他脸上，转身就走。"
    response = await client.post(
        f"/api/sessions/{session['id']}/messages", json={"content": action, "kind": "narration"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["user_message"]["message_kind"] == "narration"

    log = await client.get(f"/api/sessions/{session['id']}/messages?limit=10")
    mine = [m for m in log.json() if m["content"] == action]
    assert mine and mine[0]["message_kind"] == "narration"

    # 下一轮装配时，动作必须带着标记出现在送进模型的消息里
    prepared = await chat_service.prepare_turn(conn, session["id"], "爹，你听我说。")
    contents = [message.content for message in prepared.messages]
    assert any(f"（动作）{action}" == text for text in contents), contents


async def test_action_options_kind_must_be_known(client: AsyncClient) -> None:
    """kind 只认 text / narration，别的一律挡回去。"""

    _, session = await _setup(client)
    response = await client.post(
        f"/api/sessions/{session['id']}/messages", json={"content": "随便说说", "kind": "telepathy"}
    )
    assert response.status_code == 400, response.text
