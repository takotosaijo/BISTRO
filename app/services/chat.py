"""对话管线：装配上下文 → 生成 → 落库。

第 0 期只做 1v1 文本；群聊的发言调度留到第 2 期，但上下文装配已经按
「多人在场」的形状写好，届时只需替换 responder 的选择逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import asyncpg

from app import repository as repo
from app.config import settings
from app.errors import CharacterUnavailable, DomainError, NotFound
from app.prompt import PromptContext, build_chat_messages, build_system_prompt
from app.providers.base import LLMProvider
from app.services.relation_evolve import evolve_relations

MAX_CONTENT_LENGTH = 2000


@dataclass
class PreparedTurn:
    session: Dict[str, Any]
    anchor: Dict[str, Any]
    responder: Dict[str, Any]
    characters: List[Dict[str, Any]]
    ctx: PromptContext
    messages: List[Any]
    system_prompt: str
    user_message: Dict[str, Any]
    history: List[Dict[str, Any]] = field(default_factory=list)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DomainError(message)


async def _load_peers_relations(
    conn: asyncpg.Connection,
    persona_id: int,
    work_id: int,
    responder_id: int,
    member_ids: List[int],
) -> List[Dict[str, Any]]:
    if len(member_ids) < 2:
        return []
    return await repo.list_effective_relationships(
        conn, persona_id, work_id, from_ids=[responder_id], to_ids=member_ids
    )


async def _resolve_last_talk_anchor(
    conn: asyncpg.Connection, history: List[Dict[str, Any]], current_anchor_id: int
) -> Optional[Dict[str, Any]]:
    """找到最近一条「发生在别的时间点」的消息所在锚点，用于提示时间已推移。"""

    for msg in reversed(history):
        anchor_id = msg.get("anchor_id")
        if anchor_id and anchor_id != current_anchor_id:
            return await repo.get_anchor(conn, anchor_id)
    return None


async def prepare_turn(
    conn: asyncpg.Connection,
    session_id: int,
    content: str,
    responder_slug: Optional[str] = None,
    message_kind: str = "text",
) -> PreparedTurn:
    text = (content or "").strip()
    _require(bool(text), "消息内容不能为空")
    _require(len(text) <= MAX_CONTENT_LENGTH, f"消息过长，上限 {MAX_CONTENT_LENGTH} 字")
    _require(message_kind in ("text", "narration"), "消息类型只能是 text 或 narration")

    session = await repo.get_session(conn, session_id)
    if session is None:
        raise NotFound("会话不存在")
    _require(session["archived_at"] is None, "会话已归档")

    members = await repo.list_session_members(conn, session_id)
    character_ids = [
        m["member_id"]
        for m in members
        if m["member_kind"] == "character" and m["is_speaking_enabled"]
    ]
    _require(bool(character_ids), "这个会话里没有可以说话的角色")

    characters = await repo.get_characters_by_ids(conn, character_ids)
    by_slug = {c["slug"]: c for c in characters}
    if responder_slug:
        responder = by_slug.get(responder_slug)
        if responder is None:
            raise DomainError(f"角色 {responder_slug} 不在本会话中")
    else:
        # 第 0 期：1v1 取唯一角色；群聊暂取第一个，第 2 期换成发言调度
        responder = characters[0]

    anchor = (
        await repo.get_anchor(conn, session["pinned_anchor_id"])
        if session.get("pinned_anchor_id")
        else None
    )
    if anchor is None:
        anchor = await repo.get_current_anchor(conn, session["user_id"], session["work_id"])
    if anchor is None:
        raise NotFound("作品没有可用的时间锚点")

    states = await repo.get_character_states(conn, anchor["id"], character_ids)
    responder_state = states.get(responder["id"])
    if responder_state is None:
        raise CharacterUnavailable(f"{responder['name']}在此时间点尚无记载，请换一个时间点")

    allow_early = bool(anchor.get("allow_early_characters"))
    availability = responder_state.get("availability")
    if availability == "deceased" and not allow_early:
        raise CharacterUnavailable(
            f"{responder['name']}在{anchor['chapter_label']}已经不在人世；"
            "可在时间线设置里开启野史模式再与他说话"
        )
    if availability == "hidden" and not allow_early:
        raise CharacterUnavailable(f"{responder['name']}在此时间点还不该出现")

    persona = await repo.get_persona(conn, session["persona_id"])
    user_relation = await repo.get_user_character_relation(
        conn, session["user_id"], responder["id"]
    )
    peer_relations = await _load_peers_relations(
        conn, session["persona_id"], session["work_id"], responder["id"], character_ids
    )
    declared_relations = await repo.list_declared_relations(
        conn, session["persona_id"], character_ids, anchor_seq=anchor["seq"]
    )

    # 记忆按锚点分层：只把「此刻及之前」说过的话送进模型，之后的不进（往回拨时间线就忘掉未来）
    history = await repo.list_messages(
        conn,
        session_id,
        limit=settings.max_history_messages,
        up_to_anchor_seq=anchor["seq"],
    )
    last_talk_anchor = await _resolve_last_talk_anchor(conn, history, anchor["id"])

    ctx = PromptContext(
        character=responder,
        anchor=anchor,
        state=responder_state,
        session=session,
        persona=persona,
        user_relation=user_relation,
        peers=characters,
        peer_relations=peer_relations,
        declared_relations=declared_relations,
        last_talk_anchor=last_talk_anchor,
    )

    # 先把用户消息落库，再连同它一起装配消息列表，保证 history 与 prompt 一致
    async with conn.transaction():
        await repo.lock_session(conn, session_id)
        user_message = await repo.append_message(
            conn,
            session_id=session_id,
            sender_kind="user",
            sender_id=session["user_id"],
            content=text,
            message_kind=message_kind,
            anchor_id=anchor["id"],
        )
        await repo.touch_session(conn, session_id)

    history = await repo.list_messages(
        conn,
        session_id,
        limit=settings.max_history_messages,
        up_to_anchor_seq=anchor["seq"],
    )
    messages = build_chat_messages(
        ctx, history, responder_id=responder["id"], name_by_id={c["id"]: c["name"] for c in characters}
    )

    return PreparedTurn(
        session=session,
        anchor=anchor,
        responder=responder,
        characters=characters,
        ctx=ctx,
        messages=messages,
        system_prompt=build_system_prompt(ctx),
        user_message=user_message,
        history=history,
    )


async def stream_reply(
    prepared: PreparedTurn, provider: LLMProvider
) -> AsyncIterator[str]:
    async for chunk in provider.stream(prepared.messages, temperature=settings.llm_temperature):
        if chunk:
            yield chunk


async def persist_reply(
    conn: asyncpg.Connection, prepared: PreparedTurn, text: str, provider: LLMProvider
) -> Dict[str, Any]:
    async with conn.transaction():
        await repo.lock_session(conn, prepared.session["id"])
        reply = await repo.append_message(
            conn,
            session_id=prepared.session["id"],
            sender_kind="character",
            sender_id=prepared.responder["id"],
            content=text,
            anchor_id=prepared.anchor["id"],
            model=f"{provider.name}:{provider.model}",
        )
        await repo.touch_session(conn, prepared.session["id"])
        await repo.bump_user_character_relation(
            conn,
            prepared.session["persona_id"],
            prepared.session["user_id"],
            prepared.responder["id"],
            prepared.anchor["id"],
        )
    return reply


async def chat_once(
    conn: asyncpg.Connection,
    session_id: int,
    content: str,
    provider: LLMProvider,
    responder_slug: Optional[str] = None,
    message_kind: str = "text",
) -> Dict[str, Any]:
    """非流式的一次完整对话，测试与简单客户端用。"""

    prepared = await prepare_turn(
        conn, session_id, content, responder_slug=responder_slug, message_kind=message_kind
    )
    chunks: List[str] = []
    async for chunk in stream_reply(prepared, provider):
        chunks.append(chunk)
    reply_text = "".join(chunks).strip()
    if not reply_text:
        reply_text = "……"
    reply = await persist_reply(conn, prepared, reply_text, provider)
    outcome = await evolve_relations(
        conn,
        session=prepared.session,
        responder=prepared.responder,
        anchor=prepared.anchor,
        user_text=prepared.user_message["content"],
        reply_text=reply_text,
        message_id=reply["id"],
    )
    return {
        "user_message": prepared.user_message,
        "reply": reply,
        "anchor": {
            "chapter_label": prepared.anchor["chapter_label"],
            "name": prepared.anchor["name"],
        },
        "responder": {"slug": prepared.responder["slug"], "name": prepared.responder["name"]},
        "provider": {"name": provider.name, "model": provider.model},
        "relation_changes": outcome["relation_changes"],
        "action_options": outcome["action_options"],
    }
