"""F13 / I01 回归：角色卡必须分层。

恒定层（identity / personality / speech_style / knowledge_scope / bottom_lines /
taboos / sample_lines）对任何时间锚点都要成立；带时序的事实只能来自
`anchor_character_states` 或关系边。全书轨迹存在 `characters.canon_arc`，永不进 prompt。

这里把 **12 角色 × 12 锚点** 全扫一遍，包括「此刻还轮不到他登场」的组合——
那种组合里的剧透只是暂时看不见，等有人补了状态行就会漏出来。
"""

from __future__ import annotations

from typing import Any, Dict, List

import asyncpg

from app import repository as repo
from app.prompt import PromptContext, build_system_prompt

WORK = "shuihu-100"

# 角色是书中人，不该知道「全书」「原著」这回事。
# 注意「小说」不在表里：「不要写成小说旁白」是给模型的出戏约束，本来就该在 prompt 里。
OUT_OF_WORLD = ("全书", "原著", "读者", "作者")

# 未来事实清单：术语 -> 最早成为事实的章回号（逐条对着 db/seed.sql 的锚点状态核过）。
# 更早的锚点里出现这些词，就说明恒定层或关系边又把剧透漏了回去。
FUTURE_TERMS: Dict[str, Dict[str, int]] = {
    "song-jiang": {"招安": 71, "梁山泊主": 71},
    "lu-jun-yi": {"第二把交椅": 71, "赚上梁山": 71},
    "wu-yong": {"梁山军师": 19},
    "lin-chong": {"第六把交椅": 71, "马军五虎将": 71, "沧州": 10},
    "wu-song": {"鸳鸯楼": 31, "二龙山": 31, "步军头领": 71, "招安": 71},
    "lu-zhi-shen": {"步军头领": 71},
    "li-kui": {"步军头领": 71},
    "shi-jin": {"八骠骑": 71},
    "pan-jin-lian": {"毒杀": 31, "通奸": 31},
    "xi-men-qing": {"毒杀": 31, "私通": 31},
    "wang-po": {"毒杀": 31, "毒药": 31},
    "gao-qiu": {"构陷": 7},
}

# 同一个 slug 在一轮测试里只装一次 prompt（测试期间数据不变）
_PROMPT_CACHE: Dict[str, Dict[int, str]] = {}


async def _reader_user(conn: asyncpg.Connection, work_id: int, anchor_id: int) -> int:
    """关系要走视图读，而视图按「用户当前锚点」解析，所以每个锚点借一个只读用户。"""

    user = await repo.ensure_user(conn, f"layer-check-{anchor_id}", "分层检查")
    await repo.set_current_anchor(conn, user["id"], work_id, anchor_id)
    return user["id"]


async def prompts_by_anchor(conn: asyncpg.Connection, slug: str) -> Dict[int, str]:
    """这个角色在每个锚点上的 system prompt。

    在场者塞满其余角色，把关系边也一起拉进来——剧透不只藏在角色卡里。
    """

    if slug in _PROMPT_CACHE:
        return _PROMPT_CACHE[slug]

    work = await repo.get_work_by_slug(conn, WORK)
    characters = await repo.list_characters(conn, work["id"])
    me = next(c for c in characters if c["slug"] == slug)
    peers = [c for c in characters if c["id"] != me["id"]]

    prompts: Dict[int, str] = {}
    for anchor in await repo.list_anchors(conn, work["id"]):
        states = await repo.get_character_states(
            conn, anchor["id"], [c["id"] for c in characters]
        )
        user_id = await _reader_user(conn, work["id"], anchor["id"])
        relations = await repo.list_effective_relationships(
            conn, user_id, work["id"], [me["id"]], [c["id"] for c in peers]
        )
        ctx = PromptContext(
            character=me,
            anchor=anchor,
            state=states.get(me["id"]),
            session={"id": 0, "session_type": "direct", "is_non_canon": False},
            user_display_name="张三",
            peers=peers,
            peer_relations=relations,
        )
        prompts[anchor["seq"]] = build_system_prompt(ctx)

    _PROMPT_CACHE[slug] = prompts
    return prompts


async def _chapter_by_seq(conn: asyncpg.Connection) -> Dict[int, int]:
    work = await repo.get_work_by_slug(conn, WORK)
    return {a["seq"]: a["chapter_no"] for a in await repo.list_anchors(conn, work["id"])}


async def _card_rows(conn: asyncpg.Connection) -> Dict[str, Dict[str, Any]]:
    work = await repo.get_work_by_slug(conn, WORK)
    rows = await conn.fetch(
        """
        SELECT slug, identity, personality, knowledge_scope, bottom_lines,
               taboos, sample_lines, canon_arc
        FROM characters WHERE work_id = $1
        """,
        work["id"],
    )
    return {r["slug"]: dict(r) for r in rows}


async def _titles_and_debut(
    conn: asyncpg.Connection,
) -> tuple[Dict[str, Dict[str, int]], Dict[str, int]]:
    """每个角色的「日后才拿到的头衔」与「首次登场章回」。

    返回 (头衔 → 首次成为事实的章回, 角色 → 首次登场章回)。
    登场时就有的身份（如宋江的「郓城县押司」）不算未来头衔，不参与提前检查。
    """

    work = await repo.get_work_by_slug(conn, WORK)
    rows = await conn.fetch(
        """
        SELECT c.slug, a.chapter_no, s.status_title
        FROM anchor_character_states s
        JOIN timeline_anchors a ON a.id = s.anchor_id
        JOIN characters c ON c.id = s.character_id
        WHERE a.work_id = $1 AND s.availability = 'introduced' AND s.status_title IS NOT NULL
        ORDER BY a.chapter_no
        """,
        work["id"],
    )
    first_title: Dict[str, Dict[str, int]] = {}
    debut: Dict[str, int] = {}
    for row in rows:
        slug = row["slug"]
        debut.setdefault(slug, row["chapter_no"])
        per_char = first_title.setdefault(slug, {})
        per_char.setdefault(row["status_title"], row["chapter_no"])
    return first_title, debut


async def test_every_character_has_a_prompt_at_every_anchor(conn) -> None:
    """144 个组合都得能装配出 prompt，状态缺失也不该炸。"""

    cards = await _card_rows(conn)
    assert len(cards) == 12
    for slug in cards:
        prompts = await prompts_by_anchor(conn, slug)
        assert len(prompts) == 12, f"{slug} 只装出了 {len(prompts)} 个锚点的 prompt"
        for prompt in prompts.values():
            assert prompt.strip()
            assert "# 你是谁" in prompt


async def test_canon_arc_never_reaches_the_prompt(conn) -> None:
    """全书轨迹只给作者看：一整段都不许进 prompt。"""

    cards = await _card_rows(conn)
    leaked: List[str] = []
    for slug, card in cards.items():
        arc = (card.get("canon_arc") or "").strip()
        assert arc, f"{slug} 缺 canon_arc，全书轨迹丢了"
        for seq, prompt in (await prompts_by_anchor(conn, slug)).items():
            if arc in prompt:
                leaked.append(f"{slug}@锚点{seq}")
    assert not leaked, "canon_arc 漏进 prompt：" + "、".join(leaked)


async def test_prompt_never_mentions_future_terms(conn) -> None:
    """早期锚点里不许出现后来才成立的说法。"""

    chapters = await _chapter_by_seq(conn)
    problems: List[str] = []
    for slug, terms in FUTURE_TERMS.items():
        for seq, prompt in (await prompts_by_anchor(conn, slug)).items():
            chapter = chapters[seq]
            for term, earliest in terms.items():
                if chapter < earliest and term in prompt:
                    problems.append(
                        f"{slug}@第{chapter}回 出现「{term}」（第{earliest}回才成立）"
                    )
    assert not problems, "prompt 里提前泄露了未来：\n" + "\n".join(problems)


async def test_later_titles_never_appear_earlier(conn) -> None:
    """「此刻身份」只能取当下那一条，不能把日后的头衔提前端出来。"""

    chapters = await _chapter_by_seq(conn)
    titles, debut = await _titles_and_debut(conn)
    problems: List[str] = []
    for slug, by_title in titles.items():
        later_titles = {
            title: chapter
            for title, chapter in by_title.items()
            if chapter > debut[slug]  # 登场时就有的身份不算「日后才拿到」
        }
        for seq, prompt in (await prompts_by_anchor(conn, slug)).items():
            chapter = chapters[seq]
            for title, title_chapter in later_titles.items():
                if chapter < title_chapter and title in prompt:
                    problems.append(
                        f"{slug}@第{chapter}回 出现第{title_chapter}回的头衔「{title}」"
                    )
    assert not problems, "头衔提前出现：\n" + "\n".join(problems)


async def test_prompt_never_breaks_the_fourth_wall(conn) -> None:
    """角色不知道自己在一本书里。"""

    cards = await _card_rows(conn)
    problems: List[str] = []
    for slug in cards:
        for seq, prompt in (await prompts_by_anchor(conn, slug)).items():
            for word in OUT_OF_WORLD:
                if word in prompt:
                    problems.append(f"{slug}@锚点{seq} 出现「{word}」")
    assert not problems, "prompt 里出现出戏的词：\n" + "\n".join(problems)


async def test_relationship_note_about_zhaoan_starts_at_anchor_10(conn) -> None:
    """武松对宋江的心里话里提「招安」，那是第七十一回才有的事，不许提前。"""

    work = await repo.get_work_by_slug(conn, WORK)
    characters = {c["slug"]: c for c in await repo.list_characters(conn, work["id"])}
    wu_song = characters["wu-song"]["id"]
    song_jiang = characters["song-jiang"]["id"]
    anchors = await repo.list_anchors(conn, work["id"])

    for chapter_no, should_mention in ((23, False), (71, True)):
        anchor = next(a for a in anchors if a["chapter_no"] == chapter_no)
        user_id = await _reader_user(conn, work["id"], anchor["id"])
        relations = await repo.list_effective_relationships(
            conn, user_id, work["id"], [wu_song], [song_jiang]
        )
        assert relations, f"第{chapter_no}回 武松→宋江 的关系边丢了"
        note = relations[0]["private_note"] or ""
        assert ("招安" in note) is should_mention, f"第{chapter_no}回 心里话：{note}"
