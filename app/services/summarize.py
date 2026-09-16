"""F22：会话摘要按时间锚点分段。

规则：**每章一条摘要**（`session_summaries` 的 `UNIQUE (session_id, anchor_id)`），
随这一章的对话滚动刷新；prompt 只取「覆盖范围落在最近窗口之外」的那些当前情提要——
原话还留在窗口里的部分不再摘要一遍，免得同一段事被说两遍。

为什么要按锚点分段：记忆是按锚点分层的（DECISIONS 2026-09-16）。一条跨章节的滚动摘要会把
「第七十一回说过的话」压进「第十回的记忆」里，角色就会知道他不该知道的事。每章一条，
往回拨时间线时自然只剩「那一章及之前」的提要。

两个实现（同 F12 / F14 的思路）：
  - LLMSummarizer：真实模型压成一到三句第三人称提要
  - MockSummarizer：离线确定性输出，够 `make check` 跑

跑在哪：与关系演化一样放在**回复之后**（流式接口里是后台任务）。
摘要是增值步骤，失败不该影响对话。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.config import Settings, settings as default_settings
from app.providers.base import ChatMessage, LLMProvider

# 一次喂给模型的新消息上限。更早的内容已经在上一版摘要里了（滚动压缩），
# 不必每次把整章几百条重发一遍。
MAX_TRANSCRIPT_MESSAGES = 40

SUMMARY_SYSTEM_PROMPT = """\
你在给一场角色扮演对话写**章节提要**：把同一个时间点（同一章）里发生的一段对话，
压成一到三句第三人称提要。这份提要是给这个角色以后当记忆用的。

规矩：
1. 只写对话里真的发生过的：这个人说了什么、做了什么，角色答应了、拒绝了还是含糊过去了。
   不要推测、不要补写没出现的情节。
2. 不要评价关系亲疏，不要用「好感」「信任」「亲近」这类词，也不要写任何数值。
3. 用第三人称写，用这个人的名字，不要写「用户」。
4. 如果给了「已有提要」，把新对话并进去，重新写一版完整提要，不要重复罗列。
5. 只输出提要正文，不要标题、不要解释、不要 JSON。
"""


@dataclass
class SummaryInput:
    character_name: str          # 角色名，提要里的「他」
    user_name: str               # 这个人（身份）的名字
    transcript: str              # 这一章新攒下的对话
    previous_summary: Optional[str] = None   # 上一版提要（滚动压缩时给）


class LLMSummarizer:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def summarize(self, payload: SummaryInput) -> str:
        previous = (
            f"\n\n已有提要：{payload.previous_summary}" if payload.previous_summary else ""
        )
        messages = [
            ChatMessage("system", SUMMARY_SYSTEM_PROMPT),
            ChatMessage(
                "user",
                f"角色：{payload.character_name}\n这个人：{payload.user_name}{previous}\n\n"
                f"这一段对话：\n{payload.transcript}",
            ),
        ]
        chunks: List[str] = []
        async for chunk in self.provider.stream(messages, temperature=0.0):
            chunks.append(chunk)
        return "".join(chunks).strip()


class MockSummarizer:
    """离线规则：不读懂内容，只产出一条**确定**的提要，够跑链路与测试。

    真实场景的提要必须由模型写——规则表压不出「他答应了什么」。
    """

    async def summarize(self, payload: SummaryInput) -> str:
        first_line = (payload.transcript.strip().splitlines() or ["（没有内容）"])[0].strip()
        if payload.previous_summary:
            return f"（离线提要）{payload.previous_summary} 此后又说过：{first_line[:24]}…"
        return (
            f"（离线提要）{payload.user_name}与{payload.character_name}这一章说起："
            f"{first_line[:32]}…"
        )


def build_summarizer(cfg: Optional[Settings] = None):
    from app.providers import build_provider  # 局部导入，避免循环

    config = cfg or default_settings
    provider = build_provider(config)
    if provider.name == "mock":
        return MockSummarizer()
    return LLMSummarizer(provider)


def render_transcript(
    messages: Sequence[Dict[str, Any]], *, character_name: str, user_name: str
) -> str:
    """把消息渲染成「谁：说了什么」，旁白标成动作。"""

    lines: List[str] = []
    for message in messages:
        content = (message.get("content") or "").strip()
        if not content:
            continue
        if message.get("sender_kind") == "user":
            label = user_name
            if message.get("message_kind") == "narration":
                content = f"（动作）{content}"
        else:
            label = character_name
        lines.append(f"{label}：{content}")
    return "\n".join(lines)


async def refresh_anchor_summary(
    conn,
    *,
    session: Dict[str, Any],
    anchor: Dict[str, Any],
    responder: Dict[str, Any],
    summarizer: Any = None,
    every: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """把这一章的对话压成一条摘要（「够不够该压了」由这里判断）。

    返回新写的摘要行；还没到该压的时候、或者模型没吐出正文，返回 None。
    """

    from app import repository as repo  # 局部导入，避免循环依赖

    step = every or default_settings.summary_every
    messages = await repo.list_session_messages_at_anchor(conn, session["id"], anchor["id"])
    if len(messages) < step:
        return None

    existing = await repo.get_anchor_summary(conn, session["id"], anchor["id"])
    last_seq = messages[-1]["seq"]
    if existing:
        if existing["covered_to_seq"] >= last_seq:
            return None
        if last_seq - existing["covered_to_seq"] < step:
            return None
        fresh = [m for m in messages if m["seq"] > existing["covered_to_seq"]]
    else:
        fresh = messages

    fresh = fresh[-MAX_TRANSCRIPT_MESSAGES:]
    persona = await repo.get_persona(conn, session["persona_id"])
    user_name = (persona or {}).get("name") or "这个人"
    payload = SummaryInput(
        character_name=responder["name"],
        user_name=user_name,
        transcript=render_transcript(
            fresh, character_name=responder["name"], user_name=user_name
        ),
        previous_summary=(existing or {}).get("summary"),
    )

    summarizer = summarizer or build_summarizer()
    try:
        text = (await summarizer.summarize(payload)).strip()
    except Exception:  # noqa: BLE001 - 摘要是增值步骤，失败不该影响对话
        return None
    if not text:
        return None

    return await repo.upsert_anchor_summary(
        conn,
        session_id=session["id"],
        anchor_id=anchor["id"],
        covered_from_seq=existing["covered_from_seq"] if existing else messages[0]["seq"],
        covered_to_seq=last_seq,
        summary=text,
    )
