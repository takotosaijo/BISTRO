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
    voice: str = "user"     # 谁在做这个动作。只认 "user"——写成角色的口吻要整组丢掉

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


# 角色这一轮什么都没说（模型没吐正文时的兜底占位）——没有话可接，就不该给选项。
# 2026-09-23 的现场：空回复那一轮，模型顺手把**角色**的下一句写成了选项。
SILENT_REPLIES = {"", "……", "…", "..."}


def _reply_is_silent(reply_text: str) -> bool:
    return (reply_text or "").strip() in SILENT_REPLIES


DETECT_SYSTEM_PROMPT = """\
你在读一轮对话，要产出两样东西：关系有没有发生**离散变化**，以及要不要给这个人几个行动选项。

一、关系变化。只在这类事情真的发生时输出：相认、认亲被拒、结拜、断交、翻脸、立誓、拜师。
不要输出态度、情绪、语气上的细微波动——那些不算关系变化。

二、行动选项。**只在角色当面要一件打字给不出的东西时才给**，就两类：
  - 要实物：凭证、信物、银子、书信（例如「玉佩拿来我看」「把那封信交出来」）；
  - 要你当场做一个动作：当面发誓、跟他走、跪下、把东西放下、转过身去。

不是这两类的一律给空数组：
  - 他在**问话**（你是谁、几时没的、这话从何处听来、你娘叫什么名字）——打字就能答，不给；
  - 他在陈述、感叹、安慰、威胁、下逐客令——不给；
  - 他这一轮**什么都没说**（空回复或「……」）——不给。

上一轮已经给过一组选项时，只有他这轮提出**新的**具体索取才再给；他只是接着追问，
就给空数组。每轮都弹会把对话变成点选游戏，这个产品卖的是关系，不是任务。

只输出 JSON：
{"changes":[{"direction":"character_to_user","kind":"recognition",
"label_after":"你认下了这个失散多年的女儿","reason":"她拿出母亲留下的信物，你信了"}],
"options":[{"voice":"user","label":"把玉佩递给他","action":"我把怀里那半块玉佩双手递到他面前"}]}

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
- 3~4 个，不多不少；不该给就 "options":[]。
- 选项是**这个人**做的事，**不是角色要做什么**：action 写成这个人的第一人称句子，
  句子里的「我」必须是他自己，角色只能出现在宾语里（「我把玉佩递给他」）。
  **绝对不要替角色说话**，不要写角色要说的话、也不要写角色的心理活动。
- 每条都要填 "voice":"user"。如果你发现自己写的是角色要说的话，就把那条删掉——
  宁可不给选项，也不要给一条角色口吻的。
- 每个选项是一条**同等有效的扮演路径**，不是「正确答案」——不要写成点对了就有奖励的样子。
- 选项之间要有真正的分歧（给 / 不给 / 说丢了 / 反问他），不要四个都是一个意思。
- label 是按钮上的短话（不超过 10 字），写这个人要做什么；action 会进历史，角色以后要记得。
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
        previous_options: Optional[Sequence[str]] = None,
    ) -> TurnAnalysis:
        previous = (
            "\n上一轮你已经给过这些选项：" + "、".join(previous_options)
            if previous_options
            else ""
        )
        messages = [
            ChatMessage("system", DETECT_SYSTEM_PROMPT),
            ChatMessage(
                "user",
                f"角色：{character_name}\n"
                f"这个人的自述：{persona_summary}\n"
                f"目前的关系说法：{current_labels}{previous}\n\n"
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
        voice = str(item.get("voice") or "user").strip() or "user"
        if label and action:
            options.append(ActionOption(label=label, action=action, voice=voice))

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
    # 只在角色**当面要东西 / 要你当场做动作**时才给选项。
    # 2026-09-23 收窄：原先把「你且说」「说明白」这类问句也算成要求，而角色几乎每轮都在问，
    # 于是选项每轮都弹（27 个弹过的会话里 24 个是每轮都弹）。
    REQUEST = ("拿来", "拿与我", "取来", "交出来", "掏出来", "给我看", "与我看看",
               "发誓", "立誓", "跟我走", "随我来", "跪下", "转过身")

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
        previous_options: Optional[Sequence[str]] = None,
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
        return {"relation_changes": [], "action_options": []}
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
    previous_options: List[str] = []
    if message_id:
        previous_options = await repo.get_last_action_option_labels(
            conn, session["id"], before_message_id=message_id
        )
    analysis = TurnAnalysis()
    try:
        analysis = await detector.detect(
            character_name=responder["name"],
            persona_summary=persona_summary,
            current_labels=current_labels,
            user_text=user_text,
            reply_text=reply_text,
            previous_options=previous_options,
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

    # 两道硬闸（2026-09-23 加）：模型说该给，也得过得了这两关才放到界面上。
    #   ① 角色这轮什么都没说（空回复占位）→ 没有话可接，不给；现场就是这么写出角色口吻选项的。
    #   ② 选项写成了**角色**的口吻（voice != user）→ 整组丢。
    #      用户点了它就成了替角色说话，角色下一轮必然一头雾水（见会话 897 / 935）。
    dropped: Optional[str] = None
    if _reply_is_silent(reply_text):
        dropped = "empty_reply"
    elif any(option.voice != "user" for option in analysis.options):
        dropped = "character_voice"

    # 选项落在这条回复的 meta 上（零 schema 改动）。**无论有没有选项都要写这个键**：
    # 前端靠「键出现了」判断后台那次判定已经跑完，否则只能一直轮询。
    options = [] if dropped else [option.as_dict() for option in analysis.options]
    if message_id:
        await repo.merge_message_meta(
            conn,
            message_id,
            {"action_options": options, "action_options_dropped": dropped},
        )

    return {"relation_changes": applied, "action_options": options}
