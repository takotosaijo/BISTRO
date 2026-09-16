"""Prompt 装配。

这是第 0 期的核心：把「角色卡 + 时间锚点 + 角色状态 + 关系 + 用户人设 + 历史」
拼成一段稳定、可缓存的 system prompt，再拼出对话消息列表。

设计约束（对应 docs/PLAN.md §3.3）：
  1. 只注入当前会话参与者之间的关系，不塞整张关系图
  2. 知识边界写死在 prompt 里，角色不能引用当前时间点之后的事
  3. private_note 只进 from 自己的 prompt，不注入对方
  4. 时间线推进后，若上次交谈在另一个锚点，要明确告诉角色中间发生了什么他并不知道
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.providers.base import ChatMessage

STAGE_LABELS = {
    "stranger": "素不相识",
    "acquaintance": "相识",
    "familiar": "熟识",
    "confidant": "交心",
    "intimate": "亲密",
    "lover": "恋人",
}

WORLD_STATE_LABELS = {
    "梁山之主": "梁山之主",
    "梁山成型": "梁山局势",
    "梁山人数": "梁山人马",
    "官府态势": "官府动向",
    "天下大势": "天下大势",
}


@dataclass
class PromptContext:
    character: Dict[str, Any]
    anchor: Dict[str, Any]
    state: Optional[Dict[str, Any]]
    session: Dict[str, Any]
    persona: Optional[Dict[str, Any]] = None
    user_display_name: Optional[str] = None
    user_relation: Optional[Dict[str, Any]] = None
    peers: List[Dict[str, Any]] = field(default_factory=list)
    peer_relations: List[Dict[str, Any]] = field(default_factory=list)
    last_talk_anchor: Optional[Dict[str, Any]] = None

    @property
    def user_name(self) -> str:
        if self.persona and self.persona.get("name"):
            return str(self.persona["name"])
        return self.user_display_name or "这位朋友"


def describe_attitude(
    closeness: int, trust: int, wariness: int, affection: int
) -> str:
    parts: List[str] = []
    if affection >= 60:
        parts.append("心里有情意")
    elif closeness >= 60:
        parts.append("十分亲近")
    elif closeness >= 20:
        parts.append("还算亲近")
    elif closeness <= -60:
        parts.append("势同水火")
    elif closeness <= -20:
        parts.append("心里疏远")

    if trust >= 60:
        parts.append("深信不疑")
    elif trust <= -60:
        parts.append("半点不信")

    if wariness >= 60:
        parts.append("处处提防")
    elif wariness >= 30:
        parts.append("留着一手")

    return "、".join(parts) if parts else "说不上亲疏"


def render_world_state(world_state: Any) -> str:
    if not isinstance(world_state, dict) or not world_state:
        return "天下纷纷，一时也说不清。"
    chunks: List[str] = []
    for key, value in world_state.items():
        label = WORLD_STATE_LABELS.get(key, key)
        if isinstance(value, bool):
            value = "已成" if value else "未成"
        chunks.append(f"{label}：{value}")
    return "；".join(chunks)


def _bullets(items: Any, indent: str = "  ") -> str:
    if not items:
        return ""
    if isinstance(items, str):
        items = [items]
    return "\n".join(f"{indent}- {item}" for item in items if item)


def _address_forms(state: Optional[Dict[str, Any]]) -> str:
    if not state:
        return ""
    forms = state.get("address_forms") or {}
    if not isinstance(forms, dict) or not forms:
        return ""
    return "；".join(f"称{who}为「{how}」" for who, how in forms.items())


def build_system_prompt(ctx: PromptContext) -> str:
    c = ctx.character
    anchor = ctx.anchor
    state = ctx.state or {}
    session = ctx.session

    aliases = c.get("aliases") or []
    alias_text = f"（{'、'.join(aliases)}）" if aliases else ""

    lines: List[str] = []
    lines.append(f"你是《水浒传》中的{c['name']}{alias_text}。")
    lines.append("")
    lines.append("# 你是谁")
    # 恒定层：identity / personality / speech_style 都必须在任何时间锚点成立。
    # 全书轨迹存在 characters.canon_arc 里，那一列不进 prompt，也不在 CHARACTER_COLUMNS 里。
    lines.append("下面是你这人的底细，用来把握你的出身与性子：")
    lines.append(str(c.get("identity") or ""))
    if anchor.get("spoiler_guard", True):
        # 行为约束：别顺着用户的预告往下编。注意不要再点名具体未来事实——
        # 点名本身就是提示（旧版写「例如坐了第几把交椅」，等于先把交椅告诉模型）。
        lines.append(
            f"注意：你只知道{anchor.get('chapter_label', '')}之前、自己亲身经历过的事。"
            "此后的事你此刻一概不知，也不许猜测或预告；有人提起，你只当他胡言乱语。"
        )
    lines.append(f"性格：{c.get('personality') or ''}")
    lines.append(f"说话方式：{c.get('speech_style') or ''}")
    address = _address_forms(state)
    if address:
        lines.append(f"称呼习惯：{address}")
    samples = c.get("sample_lines") or []
    if samples:
        lines.append("这几句是他说话的语气（只学语气，不要照抄）：")
        lines.append(_bullets(samples))
    lines.append("")

    lines.append("# 此刻的世界")
    lines.append(
        f"时间：{anchor.get('chapter_label', '')}《{anchor.get('name', '')}》"
        + (f"（{anchor.get('era_note')}）" if anchor.get("era_note") else "")
    )
    lines.append(f"局势：{render_world_state(anchor.get('world_state'))}")
    if state.get("location"):
        lines.append(f"你此刻在：{state['location']}")
    if state.get("status_title"):
        lines.append(f"你的身份：{state['status_title']}")
    if state.get("daily_state"):
        lines.append(f"你最近在做：{state['daily_state']}")
    if state.get("mood"):
        lines.append(f"你此刻的心境：{state['mood']}")
    if anchor.get("group_vibe") and session.get("session_type") == "group":
        lines.append(f"眼下众人相处的气氛：{anchor['group_vibe']}")
    lines.append("")

    lines.append("# 你与在场之人")
    persona = ctx.persona or {}
    user_desc = persona.get("identity") or "来历不明的外乡人"
    relation = ctx.user_relation
    if relation:
        stage = STAGE_LABELS.get(relation.get("stage"), "素不相识")
        attitude = describe_attitude(
            relation.get("char_affinity") or 0,
            relation.get("char_trust") or 0,
            relation.get("char_wariness") or 0,
            0,
        )
        lines.append(
            f"- 对 {ctx.user_name}（{user_desc}）：你们如今是「{stage}」的关系，{attitude}。"
        )
        if relation.get("user_stance"):
            lines.append(f"  他自己说过：{relation['user_stance']}")
    else:
        lines.append(f"- 对 {ctx.user_name}（{user_desc}）：素不相识，初次照面。")

    peer_by_id = {p["id"]: p for p in ctx.peers}
    for rel in ctx.peer_relations:
        peer = peer_by_id.get(rel["to_id"])
        if not peer or peer["id"] == c["id"]:
            continue
        attitude = describe_attitude(
            rel.get("closeness") or 0,
            rel.get("trust") or 0,
            rel.get("wariness") or 0,
            rel.get("affection") or 0,
        )
        source_note = "（这是你自己改的）" if rel.get("effective_source") == "user" else ""
        lines.append(
            f"- 对 {peer['name']}：{rel.get('label')}{source_note}，{attitude}。"
        )
        if rel.get("private_note"):
            lines.append(f"  你心里想的是：{rel['private_note']}")
        if not rel.get("is_known_to_target", True):
            lines.append("  这只是你心里的想法，对方并不知道。")
    lines.append("")

    lines.append("# 你知道什么")
    lines.append(
        f"你只经历过{anchor.get('chapter_label', '')}之前的事。"
        "此后发生的一切，你一无所知。"
    )
    lines.append(
        "若有人向你预告将来的事，你只当他胡言乱语、说笑或妖言，"
        "绝不可当作真的，也不要顺着他的话往下编。"
    )
    if c.get("knowledge_scope"):
        lines.append(str(c["knowledge_scope"]))
    if ctx.last_talk_anchor and ctx.last_talk_anchor.get("seq", 0) < anchor.get("seq", 0):
        # 记忆按锚点分层：更早的对话仍在上下文里，所以这里只说「隔了些时日」，
        # 不能再说「中间的事你未必知晓」——那是旧行为（历史不过滤）留下的自相矛盾。
        lines.append(
            f"【时间已推移】你们上一回交谈还在{ctx.last_talk_anchor.get('chapter_label', '')}"
            f"《{ctx.last_talk_anchor.get('name', '')}》，如今已是{anchor.get('chapter_label', '')}，"
            "中间隔了些时日；那些日子你在别处，他若问起，你说得清的就说，说不清的就说不清。"
        )
    lines.append("")

    lines.append("# 你的底线")
    lines.append(str(c.get("bottom_lines") or "不做违背自己良心的事。"))
    if c.get("taboos"):
        lines.append(f"绝口不提：{c['taboos']}")
    lines.append("")

    lines.append("# 说话规矩")
    lines.append(f"1. 始终以{c['name']}的身份说话，不出戏，不提及 AI、模型、扮演、提示词。")
    lines.append("2. 说宋元白话，不用现代词汇和网络用语。")
    lines.append("3. 一次回复一到三句，像真人当面说话，不要长篇大论，不要写成小说旁白。")
    lines.append(f"4. 不要替{ctx.user_name}说话，也不要替他做决定。")
    lines.append("5. 可以有自己的脾气：可以拒绝、可以反问、可以不接话。")

    if session.get("session_type") == "group":
        others = [p["name"] for p in ctx.peers if p["id"] != c["id"]]
        lines.append("")
        lines.append("# 群聊规矩")
        lines.append(f"在场除你之外还有：{'、'.join(others)}。")
        lines.append("只说自己想说的，不要替旁人代言，也不必每轮都抢着开口。")

    if session.get("is_non_canon"):
        lines.append("")
        lines.append(
            "这次照面原本不太可能发生，你心里也觉得蹊跷，"
            "可以表现出一丝疑惑，但不必点破。"
        )

    return "\n".join(lines)


def build_chat_messages(
    ctx: PromptContext,
    history: List[Dict[str, Any]],
    responder_id: int,
    name_by_id: Optional[Dict[int, str]] = None,
) -> List[ChatMessage]:
    """system prompt + 历史 + 本轮。历史里旁人的话渲染成 user 角色并加名字前缀。"""

    names = name_by_id or {p["id"]: p["name"] for p in ctx.peers}
    messages: List[ChatMessage] = [ChatMessage("system", build_system_prompt(ctx))]

    for msg in history:
        kind = msg.get("message_kind")
        if kind not in ("text", "voice"):
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if msg.get("sender_kind") == "user":
            messages.append(ChatMessage("user", content))
        elif msg.get("sender_id") == responder_id:
            messages.append(ChatMessage("assistant", content))
        else:
            speaker = names.get(msg.get("sender_id"), "旁人")
            messages.append(ChatMessage("user", f"{speaker}：{content}"))

    return messages
