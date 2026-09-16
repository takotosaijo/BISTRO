"""F12：从「我在这个故事里是谁」里读出他与角色之间的关系。

按 DECISIONS 2026-09-16：保存身份时解析**一次**，结果落成关系边（两个方向各一条），
prompt 以它为准；用户手改过的边（`is_manual=true`）在重新解析时不被覆盖。

为什么要解析而不是让模型每次现场理解：同一段自述今天理解成「邻居」、明天成「陌生人」，
不可复现；而且拿不到可展示、可手改、将来可用来推进阶段的结构化结果。

两个实现：
  - LLMRelationParser：真实模型，读懂自然语言（「私生女」「当年未及提亲的旧相识」）
  - MockRelationParser：关键词规则，离线可跑（make check 用它，保证链路可验证）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.config import Settings, settings as default_settings
from app.providers.base import ChatMessage, LLMProvider


@dataclass
class DeclaredRelation:
    """一个身份与一个角色之间的声明（双向各一个说法）。"""

    character_name: str
    user_label: str          # 用户眼里的角色：「他是我爹」
    character_label: str     # 角色眼里的用户：「不认得这个姑娘」
    character_knows: bool    # 这个角色一开始是否认得这个身份
    closeness: int = 0
    trust: int = 0
    wariness: int = 0
    affection: int = 0
    user_stance: Optional[str] = None   # 用户的态度原话，进 prompt
    reason: Optional[str] = None


PARSE_SYSTEM_PROMPT = """\
你在读一个人给自己写的「我在这个故事里是谁」，判断他与指定小说角色之间的关系。
只输出 JSON，不要任何解释文字。格式：
{"relations":[{"character":"林冲","user_view":"他是我爹","character_view":"不认得这个姑娘",
"character_knows":false,"closeness":60,"trust":50,"wariness":0,"affection":70,
"user_stance":"我是他失散多年的女儿，想认下他","reason":"她自称私生女"}]}

规则：
1. 只为自述里**能推出来**的角色输出条目。推不出来就别写（例如自述只说自己是酒铺掌柜、
   路过的客人），数组留空。
2. 两个方向分别写，视角不要混：
   - user_view＝**用户视角**看角色，用第一人称：「他是我爹」「他是我在东京时的旧相识」。
   - character_view＝**角色视角**看用户，用第三人称写这个用户，**不要出现「我」**：
     「不认得这个姑娘，她自称是你的骨肉」「当年未及提亲就出了事的旧相识，如今寻了来」。
   两边可以完全不同：写「私生女」时，用户认得他，而他不认得用户。
3. character_knows：这个角色一开场是否认得这个身份。
4. closeness/trust/wariness/affection 取值 -100..100，按常识给：父女、旧情人这类给高值，
   陌生人给 0。wariness 是戒备。
5. 不要编造自述里没有的关系，也不要引入原著结局。
"""


class LLMRelationParser:
    """真实模型解析。输出不合法就当没解析出关系，不影响保存。"""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def parse(
        self, self_description: str, character_names: Sequence[str]
    ) -> List[DeclaredRelation]:
        messages = [
            ChatMessage("system", PARSE_SYSTEM_PROMPT),
            ChatMessage(
                "user",
                f"可选角色：{'、'.join(character_names)}\n\n自述：{self_description}",
            ),
        ]
        chunks: List[str] = []
        async for chunk in self.provider.stream(messages, temperature=0.0):
            chunks.append(chunk)
        return parse_relations_json("".join(chunks))


def parse_relations_json(text: str) -> List[DeclaredRelation]:
    """从模型输出里抠出 JSON。允许它带 ```json 围栏或前后废话。"""

    if not text:
        return []
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []

    out: List[DeclaredRelation] = []
    for item in payload.get("relations") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("character") or "").strip()
        if not name:
            continue
        out.append(
            DeclaredRelation(
                character_name=name,
                user_label=str(item.get("user_view") or "").strip() or "认得他",
                character_label=str(item.get("character_view") or "").strip() or "认得此人",
                character_knows=bool(item.get("character_knows", True)),
                closeness=_clamp(item.get("closeness")),
                trust=_clamp(item.get("trust")),
                wariness=_clamp(item.get("wariness")),
                affection=_clamp(item.get("affection")),
                user_stance=(item.get("user_stance") or None),
                reason=(item.get("reason") or None),
            )
        )
    return out


def _clamp(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(-100, min(100, number))


class MockRelationParser:
    """离线规则解析：只覆盖几个明显情形，够跑测试与演示。

    真实场景请用 LLMRelationParser——自然语言的关系千奇百怪，规则表列不完。
    """

    KIN = ("女儿", "私生女", "儿子", "亲爹", "亲娘", "父亲", "母亲", "父女", "母子", "骨肉")
    LOVER = ("情人", "相好", "旧识", "旧相知", "青梅", "旧情", "未及提亲", "结发")
    NEIGHBOR = ("邻居", "街坊", "同乡", "邻居家的")

    async def parse(
        self, self_description: str, character_names: Sequence[str]
    ) -> List[DeclaredRelation]:
        text = self_description or ""
        # 只对自述里**点名提到**的角色下结论：规则表没法猜「私生女」是谁的私生女
        mentioned = [name for name in character_names if name and name in text]
        if not mentioned:
            return []
        if any(word in text for word in self.KIN):
            return [
                DeclaredRelation(
                    character_name=name,
                    user_label="他（她）是我的血亲长辈",
                    character_label="并不认得这个年轻人，他自称是你的骨肉",
                    character_knows=False,
                    closeness=70,
                    trust=60,
                    wariness=10,
                    affection=80,
                    user_stance="我是来认亲的",
                    reason="自述里提到血缘关系",
                )
                for name in mentioned
            ]
        if any(word in text for word in self.LOVER):
            return [
                DeclaredRelation(
                    character_name=name,
                    user_label="他是我当年的旧相识",
                    character_label="当年未及提亲就出了事的旧相识，如今寻了来",
                    character_knows=True,
                    closeness=55,
                    trust=50,
                    wariness=20,
                    affection=65,
                    user_stance="我寻了你很久",
                    reason="自述里提到旧日情分",
                )
                for name in mentioned
            ]
        if any(word in text for word in self.NEIGHBOR):
            return [
                DeclaredRelation(
                    character_name=name,
                    user_label="街坊邻居",
                    character_label="街坊邻居，见过几面",
                    character_knows=True,
                    closeness=20,
                    trust=20,
                    wariness=10,
                    affection=10,
                    reason="自述里提到同乡或邻舍",
                )
                for name in mentioned
            ]
        return []


async def sync_declared_relations(
    conn, persona: Dict[str, Any], parser: Any = None
) -> List[Dict[str, Any]]:
    """解析身份自述里与各角色的关系，落成声明边（F12）。

    接口层（保存身份时）与开发脚本都调它；解析失败不影响保存——
    身份本身已经写进去了，关系下次再解析。
    """

    from app import repository as repo  # 局部导入，避免循环依赖

    parser = parser or build_relation_parser()
    characters = await repo.list_characters(conn, persona["work_id"])
    description = "；".join(
        part
        for part in (
            persona.get("name"),
            persona.get("identity"),
            persona.get("background"),
            persona.get("appearance"),
            persona.get("speech_style"),
        )
        if part
    )
    try:
        parsed = await parser.parse(description, [c["name"] for c in characters])
    except Exception:  # noqa: BLE001 - 解析是增值步骤，不该让保存失败
        return []

    by_name = {c["name"]: c for c in characters}
    stored: List[Dict[str, Any]] = []
    for relation in parsed:
        character = by_name.get(relation.character_name)
        if character is None:
            continue
        await repo.replace_declared_relation(
            conn,
            persona["id"],
            persona["work_id"],
            character["id"],
            user_label=relation.user_label,
            user_stance=relation.user_stance,
            character_label=relation.character_label,
            character_knows=relation.character_knows,
            closeness=relation.closeness,
            trust=relation.trust,
            wariness=relation.wariness,
            affection=relation.affection,
        )
        stored.append(
            {
                "character": character["slug"],
                "character_name": character["name"],
                "user_label": relation.user_label,
                "character_label": relation.character_label,
                "character_knows": relation.character_knows,
                "reason": relation.reason,
            }
        )
    return stored


def build_relation_parser(cfg: Optional[Settings] = None):
    """按 provider 选解析器：mock 用规则，真实模型用 LLM。"""

    from app.providers import build_provider  # 局部导入，避免循环

    config = cfg or default_settings
    provider = build_provider(config)
    if provider.name == "mock":
        return MockRelationParser()
    return LLMRelationParser(provider)
