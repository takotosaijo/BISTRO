"""F14 关系演化 + F26 情境选项——同一次模型调用读完这一轮，产出两样东西。

按 DECISIONS 2026-09-16「关系演化走边版本化」：关系变了就往 `relationship_edges`
插一条新边（`valid_from_anchor_id` = 变化发生的锚点），旧边补上 `valid_to_anchor_id`。
渲染时按当前锚点取生效中的那一条——**滑回变化之前，旧说法自动生效**，不是特判。

按 DECISIONS 2026-09-16「情境选项」：角色**明确提要求**时（要凭证、要银子、要你发誓、
要你跟他走），顺带给出 3~4 个行动选项。这两件事共用同一次调用——不然每轮要多花两次请求，
而且「他提出了要求」本身就是判断「有没有关系变化」时要读的同一段话。

两个实现（同 F12 的思路）：
  - LLMChangeDetector：真实模型读这一轮对话，判断离散变化 + 给行动选项
  - MockChangeDetector：离线规则，够跑测试（变化只认「自称血亲 + 对方接了话」）

判定与写入分开：**模型只负责标记发生了什么**，写边与写审计由代码执行。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.config import Settings, settings as default_settings
from app.providers.base import ChatMessage, LLMProvider


@dataclass
class RelationChange:
    direction: str          # user_to_character | character_to_user
    label_after: str        # 新的说法
    reason: Optional[str] = None
    kind: str = "other"     # 变化类型，见 CHANGE_KINDS（F14 验针靠它判定「认下」，不靠抠字眼）


# 变化类型：模型必须从这几个里挑一个。有了它，「他到底认没认」就是个**结构化判定**，
# 不用去猜 label 的措辞——试过一次按关键词猜，把「却仍不肯当面认下这个女儿」当成了认下。
# 注意 deferral（日后再认 / 此刻认不得）**不是** recognition。
CHANGE_KINDS = (
    "recognition",       # 相认、认亲、认作骨肉
    "rejection",         # 认亲被拒，明确不认
    "deferral",          # 推后：日后再认、此刻认不得
    "sworn_brotherhood", # 结拜、认作兄弟
    "falling_out",       # 翻脸、断交
    "oath",              # 立誓、拜师
    "other",
)


@dataclass
class ActionOption:
    """一个行动选项：按钮上显示 label，点下去把 action 作为旁白消息发出去。"""

    label: str
    action: str

    def as_dict(self) -> Dict[str, str]:
        return {"label": self.label, "action": self.action}


@dataclass
class TurnAnalysis:
    """这一轮读出来的结果：关系有没有变 + 要不要给行动选项。"""

    changes: List[RelationChange] = field(default_factory=list)
    options: List[ActionOption] = field(default_factory=list)


# 选项数量由产品定死 3~4（DECISIONS 2026-09-16）：不让模型自由发挥数量，
# 数量漂移会让界面与交互不可预期。模型给多了截断，给不够就当它没找准要求。
MIN_OPTIONS = 3
MAX_OPTIONS = 4


def _normalize_options(options: Sequence[ActionOption]) -> List[ActionOption]:
    seen: List[str] = []
    unique: List[ActionOption] = []
    for option in options:
        label = (option.label or "").strip()
        action = (option.action or "").strip()
        if not label or not action or label in seen:
            continue
        seen.append(label)
        unique.append(ActionOption(label=label, action=action))
    if len(unique) < MIN_OPTIONS:
        return []          # 宁可不给，也不给一个「只有一条路」的假选择
    return unique[:MAX_OPTIONS]


DETECT_SYSTEM_PROMPT = """\
你在读一轮对话，要产出两样东西：关系有没有发生**离散变化**，以及要不要给这个人几个行动选项。

一、关系变化。只在这类事情真的发生时输出：相认、认亲被拒、结拜、断交、翻脸、立誓、拜师。
不要输出态度、情绪、语气上的细微波动——那些不算关系变化。

二、行动选项。只有当角色在这一轮**明确提出要求或条件**时（要凭证、要银子、要你发誓、
要你跟他走、要你说清楚某件事）才给，给 3~4 个；他只是寻常说话就给空数组。

只输出 JSON：
{"changes":[{"direction":"character_to_user","kind":"recognition",
"label_after":"你认下了这个失散多年的女儿","reason":"她拿出母亲留下的信物，你信了"}],
"options":[{"label":"把玉佩递给他","action":"我把怀里那半块玉佩双手递到他面前"}]}

direction 二选一：
- user_to_character：这个人现在怎么看角色（用「他」指角色）
- character_to_user：角色现在怎么看这个人（用「你」指角色自己，用「他/她」指这个人）

kind 必须从下面挑一个（不要自创）：
- recognition：相认、认亲、认作骨肉。
- rejection：认亲被拒，明确不认。
- deferral：把话说推后了——「日后再认」「此刻认不得」「等有朝一日」。这**不是** recognition。
- sworn_brotherhood：结拜、认作兄弟。
- falling_out：翻脸、绝交、断交。
- oath：立誓、拜师。
- other：其它离散变化。

判定只看角色这一轮**做了什么**，不看这个人的话有多动人：他只是被说动、还在追问，
都不算变化；他说「我信你」但仍不肯认，那是 deferral 或 other。

label_after 是一句**关系说法**，写成角色视角的短句（「你认下了这个失散多年的女儿」）。
没有关系变化就输出 "changes":[]。

选项的规矩：
- 3~4 个，不多不少；没有明确要求就 "options":[]。
- 每个选项是一条**同等有效的扮演路径**，不是「正确答案」——不要写成点对了就有奖励的样子。
- 选项之间要有真正的分歧（给 / 不给 / 说丢了 / 反问他），不要四个都是一个意思。
- label 是按钮上的短话（不超过 10 字）；action 用**第一人称**写这个人做的动作或说的话，
  它会进历史，角色以后要记得。
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
    ) -> TurnAnalysis:
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
        return parse_turn_analysis("".join(chunks))


def parse_turn_analysis(text: str) -> TurnAnalysis:
    """从模型输出里抠出 JSON。允许它带 ```json 围栏或前后废话。"""

    if not text:
        return TurnAnalysis()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return TurnAnalysis()
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return TurnAnalysis()

    changes: List[RelationChange] = []
    for item in payload.get("changes") or []:
        if not isinstance(item, dict):
            continue
        direction = str(item.get("direction") or "").strip()
        label = str(item.get("label_after") or "").strip()
        if direction not in ("user_to_character", "character_to_user") or not label:
            continue
        kind = str(item.get("kind") or "").strip()
        if kind not in CHANGE_KINDS:
            kind = "other"
        changes.append(
            RelationChange(
                direction=direction,
                label_after=label,
                reason=item.get("reason") or None,
                kind=kind,
            )
        )

    options: List[ActionOption] = []
    for item in payload.get("options") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        action = str(item.get("action") or item.get("text") or "").strip()
        if label and action:
            options.append(ActionOption(label=label, action=action))

    return TurnAnalysis(changes=changes, options=_normalize_options(options))


class MockChangeDetector:
    """离线规则：只认「自称血亲 + 对方接了话」这一种变化。

    为什么规则这么松：mock provider 会把用户的话原样回显，所以只要用户说了「认」，
    回复里就一定有「认」。真实场景的判定交给 LLMChangeDetector——自然语言的关系变化
    （翻脸、拜师、断交）规则表列不完。

    选项同理：真实模型读「他是不是在提要求」，离线只认几个明显的索要词。
    """

    KIN_CLAIM = ("女儿", "儿子", "亲爹", "亲娘", "认亲", "骨肉", "血脉")
    ACCEPT = ("认", "信", "记下", "既如此", "罢了")
    REJECT = ("不认", "休要", "胡言", "莫要")
    REQUEST = ("拿来", "拿与我", "取来", "给我", "你且说", "说明白", "发誓", "立誓",
               "银子", "凭证", "信物", "跟我走", "须得")

    # 四条同等有效的路子：给 / 不给 / 反问 / 拖一拖（对应 DECISIONS 里那个玉佩的例子）
    OPTIONS = (
        ActionOption(label="照他说的做", action="我不再迟疑，把该拿的凭证双手递到他面前。"),
        ActionOption(label="推说没有", action="我摇头，只说自己手里什么都没有。"),
        ActionOption(label="反问一句", action="我没有立刻答话，反问他为何非要这样东西不可。"),
        ActionOption(label="先退一步", action="我后退半步，只说这事容我想一想。"),
    )

    async def detect(
        self,
        *,
        character_name: str,
        persona_summary: str,
        current_labels: str,
        user_text: str,
        reply_text: str,
    ) -> TurnAnalysis:
        changes: List[RelationChange] = []
        if not any(word in user_text for word in self.KIN_CLAIM):
            pass
        elif any(word in reply_text for word in self.REJECT):
            changes = [
                RelationChange(
                    direction="character_to_user",
                    label_after="你并不认这门亲",
                    reason="对方自称血亲，你没有认下",
                    kind="rejection",
                )
            ]
        elif any(word in reply_text for word in self.ACCEPT):
            changes = [
                RelationChange(
                    direction="character_to_user",
                    label_after="你已认下这门亲",
                    reason="对方自称血亲，你接了这个话",
                    kind="recognition",
                )
            ]

        options = (
            list(self.OPTIONS) if any(word in reply_text for word in self.REQUEST) else []
        )
        return TurnAnalysis(changes=changes, options=options)


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
) -> Dict[str, Any]:
    """读完这一轮：有变化就写新边版本 + 审计，有要求就把行动选项挂到那条回复上。

    返回 {"relation_changes": [...], "action_options": [...]}。
    关系变化与行动选项共用这一次调用（DECISIONS 2026-09-16）——不能让每轮多花两次请求。
    """

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
    analysis = TurnAnalysis()
    try:
        analysis = await detector.detect(
            character_name=responder["name"],
            persona_summary=persona_summary,
            current_labels=current_labels,
            user_text=user_text,
            reply_text=reply_text,
        )
    except Exception:  # noqa: BLE001 - 演化是增值步骤，失败不该影响对话
        analysis = TurnAnalysis()

    applied: List[Dict[str, Any]] = []
    for change in analysis.changes:
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
            applied.append({**row, "kind": change.kind})

    # 选项落在这条回复的 meta 上（零 schema 改动）。**无论有没有选项都要写这个键**：
    # 前端靠「键出现了」判断后台那次判定已经跑完，否则只能一直轮询。
    options = [option.as_dict() for option in analysis.options]
    if message_id:
        await repo.merge_message_meta(conn, message_id, {"action_options": options})

    return {"relation_changes": applied, "action_options": options}
