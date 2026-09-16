"""F14：关系演化——把「相认 / 结拜 / 翻脸」写成新的边版本。

按 DECISIONS 2026-09-16「关系演化走边版本化」：关系变了就往 `relationship_edges`
插一条新边（`valid_from_anchor_id` = 变化发生的锚点），旧边补上 `valid_to_anchor_id`。
渲染时按当前锚点取生效中的那一条——**滑回变化之前，旧说法自动生效**，不是特判。

两个实现（同 F12 的思路）：
  - LLMChangeDetector：真实模型读这一轮对话，判断有没有发生离散变化
  - MockChangeDetector：离线规则，只认「自称血亲 + 对方接了话」这一种，够跑测试

判定与写入分开：**模型只负责标记发生了什么**，写边与写审计由代码执行。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.config import Settings, settings as default_settings
from app.providers.base import ChatMessage, LLMProvider


@dataclass
class RelationChange:
    direction: str          # user_to_character | character_to_user
    label_after: str        # 新的说法
    reason: Optional[str] = None


DETECT_SYSTEM_PROMPT = """\
你在读一轮对话，判断「这个角色」与「这个人」之间的关系有没有发生**离散变化**。

只在这类事情真的发生时输出：相认、认亲被拒、结拜、断交、翻脸、立誓、拜师。
不要输出态度、情绪、语气上的细微波动——那些不算关系变化。

只输出 JSON：
{"changes":[{"direction":"character_to_user","label_after":"你认下了这个失散多年的女儿",
"reason":"她拿出母亲留下的信物，你信了"}]}

direction 二选一：
- user_to_character：这个人现在怎么看角色（用「他」指角色）
- character_to_user：角色现在怎么看这个人（用「你」指角色自己，用「他/她」指这个人）

label_after 是一句**关系说法**，写成角色视角的短句（「你认下了这个失散多年的女儿」）。
没有变化就输出 {"changes":[]}。
"""


class LLMChangeDetector:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def detect(
        self,
        *,
        character_name: str,
        persona_summary: str,
        current_labels: str,
        user_text: str,
        reply_text: str,
    ) -> List[RelationChange]:
        messages = [
            ChatMessage("system", DETECT_SYSTEM_PROMPT),
            ChatMessage(
                "user",
                f"角色：{character_name}\n"
                f"这个人的自述：{persona_summary}\n"
                f"目前的关系说法：{current_labels}\n\n"
                f"这一轮他说：{user_text}\n"
                f"角色回答：{reply_text}",
            ),
        ]
        chunks: List[str] = []
        async for chunk in self.provider.stream(messages, temperature=0.0):
            chunks.append(chunk)
        return parse_changes_json("".join(chunks))


def parse_changes_json(text: str) -> List[RelationChange]:
    if not text:
        return []
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []

    out: List[RelationChange] = []
    for item in payload.get("changes") or []:
        if not isinstance(item, dict):
            continue
        direction = str(item.get("direction") or "").strip()
        label = str(item.get("label_after") or "").strip()
        if direction not in ("user_to_character", "character_to_user") or not label:
            continue
        out.append(
            RelationChange(direction=direction, label_after=label, reason=item.get("reason") or None)
        )
    return out


class MockChangeDetector:
    """离线规则：只认「自称血亲 + 对方接了话」这一种变化。

    为什么规则这么松：mock provider 会把用户的话原样回显，所以只要用户说了「认」，
    回复里就一定有「认」。真实场景的判定交给 LLMChangeDetector——自然语言的关系变化
    （翻脸、拜师、断交）规则表列不完。
    """

    KIN_CLAIM = ("女儿", "儿子", "亲爹", "亲娘", "认亲", "骨肉", "血脉")
    ACCEPT = ("认", "信", "记下", "既如此", "罢了")
    REJECT = ("不认", "休要", "胡言", "莫要")

    async def detect(
        self,
        *,
        character_name: str,
        persona_summary: str,
        current_labels: str,
        user_text: str,
        reply_text: str,
    ) -> List[RelationChange]:
        if not any(word in user_text for word in self.KIN_CLAIM):
            return []
        if any(word in reply_text for word in self.REJECT):
            return [
                RelationChange(
                    direction="character_to_user",
                    label_after="你并不认这门亲",
                    reason="对方自称血亲，你没有认下",
                )
            ]
        if any(word in reply_text for word in self.ACCEPT):
            return [
                RelationChange(
                    direction="character_to_user",
                    label_after="你已认下这门亲",
                    reason="对方自称血亲，你接了这个话",
                )
            ]
        return []


def build_change_detector(cfg: Optional[Settings] = None):
    from app.providers import build_provider  # 局部导入，避免循环

    config = cfg or default_settings
    provider = build_provider(config)
    if provider.name == "mock":
        return MockChangeDetector()
    return LLMChangeDetector(provider)


async def evolve_relations(
    conn,
    *,
    session: Dict[str, Any],
    responder: Dict[str, Any],
    anchor: Dict[str, Any],
    user_text: str,
    reply_text: str,
    message_id: Optional[int] = None,
    detector: Any = None,
) -> List[Dict[str, Any]]:
    """跑一次变化检测；有变化就写新边版本 + 审计。返回写下的变化清单。"""

    from app import repository as repo  # 局部导入，避免循环依赖

    persona = await repo.get_persona(conn, session["persona_id"])
    if persona is None:
        return []
    declared = await repo.list_declared_relations(
        conn, session["persona_id"], [responder["id"]], anchor_seq=anchor["seq"]
    )
    current_labels = "；".join(
        f"{'他看角色' if rel['from_kind'] == 'user' else '角色看他'}：{rel['label']}"
        for rel in declared
    ) or "（还没有声明过关系）"
    persona_summary = "；".join(
        part for part in (persona.get("name"), persona.get("identity"), persona.get("background")) if part
    )

    detector = detector or build_change_detector()
    try:
        changes = await detector.detect(
            character_name=responder["name"],
            persona_summary=persona_summary,
            current_labels=current_labels,
            user_text=user_text,
            reply_text=reply_text,
        )
    except Exception:  # noqa: BLE001 - 演化是增值步骤，失败不该影响对话
        return []

    applied: List[Dict[str, Any]] = []
    for change in changes:
        row = await repo.version_declared_relation(
            conn,
            persona_id=session["persona_id"],
            work_id=session["work_id"],
            character_id=responder["id"],
            direction=change.direction,
            label_after=change.label_after,
            anchor_id=anchor["id"],
            anchor_seq=anchor["seq"],
            reason=change.reason,
            message_id=message_id,
        )
        if row:
            applied.append(row)
    return applied
