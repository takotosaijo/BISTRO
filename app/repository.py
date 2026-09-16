"""数据访问层。

刻意保持为「薄薄一层 SQL」，让 db/schema.sql 继续作为唯一事实来源，
不在 Python 里再维护一套 ORM 模型。所有涉及时间线与关系覆盖的解析都交给
数据库视图（v_effective_relationships / v_character_states_resolved），
应用层不做重复实现。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import asyncpg


def row_to_dict(row: Optional[asyncpg.Record]) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Sequence[asyncpg.Record]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 作品 / 时间线
# ---------------------------------------------------------------------------


async def get_work_by_slug(conn: asyncpg.Connection, slug: str) -> Optional[Dict[str, Any]]:
    return row_to_dict(
        await conn.fetchrow(
            "SELECT id, slug, title, edition, total_chapters FROM works WHERE slug = $1", slug
        )
    )


async def list_anchors(conn: asyncpg.Connection, work_id: int) -> List[Dict[str, Any]]:
    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT id, seq, chapter_no, chapter_label, name, era_note,
                   world_state, group_vibe, summary
            FROM timeline_anchors
            WHERE work_id = $1
            ORDER BY seq
            """,
            work_id,
        )
    )


async def get_anchor(conn: asyncpg.Connection, anchor_id: int) -> Optional[Dict[str, Any]]:
    return row_to_dict(
        await conn.fetchrow(
            """
            SELECT id, work_id, seq, chapter_no, chapter_label, name, era_note,
                   world_state, group_vibe, summary
            FROM timeline_anchors WHERE id = $1
            """,
            anchor_id,
        )
    )


async def get_current_anchor(
    conn: asyncpg.Connection, user_id: int, work_id: int
) -> Optional[Dict[str, Any]]:
    """取用户当前时间锚点。没有设置过则回退到最早锚点（且不落库）。"""

    row = await conn.fetchrow(
        """
        SELECT a.id, a.seq, a.chapter_no, a.chapter_label, a.name, a.era_note,
               a.world_state, a.group_vibe, a.summary,
               ts.canon_lock, ts.allow_early_characters, ts.spoiler_guard
        FROM user_timeline_settings ts
        JOIN timeline_anchors a ON a.id = ts.current_anchor_id
        WHERE ts.user_id = $1 AND ts.work_id = $2
        """,
        user_id,
        work_id,
    )
    if row is not None:
        return dict(row)

    fallback = await conn.fetchrow(
        """
        SELECT id, seq, chapter_no, chapter_label, name, era_note, world_state,
               group_vibe, summary,
               true AS canon_lock, false AS allow_early_characters, true AS spoiler_guard
        FROM timeline_anchors WHERE work_id = $1 ORDER BY seq LIMIT 1
        """,
        work_id,
    )
    return row_to_dict(fallback)


async def set_current_anchor(
    conn: asyncpg.Connection, user_id: int, work_id: int, anchor_id: int
) -> None:
    await conn.execute(
        """
        INSERT INTO user_timeline_settings (user_id, work_id, current_anchor_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id, work_id)
        DO UPDATE SET current_anchor_id = EXCLUDED.current_anchor_id, updated_at = now()
        """,
        user_id,
        work_id,
        anchor_id,
    )


# ---------------------------------------------------------------------------
# 用户与人设
# ---------------------------------------------------------------------------


async def ensure_user(
    conn: asyncpg.Connection, external_id: str, display_name: Optional[str] = None
) -> Dict[str, Any]:
    row = await conn.fetchrow(
        """
        INSERT INTO users (external_id, display_name) VALUES ($1, $2)
        ON CONFLICT (external_id) DO UPDATE SET display_name = COALESCE(EXCLUDED.display_name, users.display_name)
        RETURNING id, external_id, display_name
        """,
        external_id,
        display_name,
    )
    return dict(row)


async def get_user(conn: asyncpg.Connection, user_id: int) -> Optional[Dict[str, Any]]:
    return row_to_dict(
        await conn.fetchrow(
            "SELECT id, external_id, display_name FROM users WHERE id = $1", user_id
        )
    )


PERSONA_COLUMNS = """
  id, user_id, work_id, name, identity, background, appearance, speech_style,
  free_note, is_archived, created_at, updated_at
"""


async def list_personas(
    conn: asyncpg.Connection, user_id: int, work_id: int
) -> List[Dict[str, Any]]:
    """一个账号在这个作品里的所有身份。"""

    return rows_to_dicts(
        await conn.fetch(
            f"""
            SELECT {PERSONA_COLUMNS} FROM personas
            WHERE user_id = $1 AND work_id = $2 AND NOT is_archived
            ORDER BY id
            """,
            user_id,
            work_id,
        )
    )


async def get_persona(
    conn: asyncpg.Connection, persona_id: int
) -> Optional[Dict[str, Any]]:
    return row_to_dict(
        await conn.fetchrow(
            f"SELECT {PERSONA_COLUMNS} FROM personas WHERE id = $1", persona_id
        )
    )


async def create_persona(
    conn: asyncpg.Connection,
    user_id: int,
    work_id: int,
    name: str,
    identity: Optional[str] = None,
    background: Optional[str] = None,
    appearance: Optional[str] = None,
    speech_style: Optional[str] = None,
    free_note: Optional[str] = None,
) -> Dict[str, Any]:
    return dict(
        await conn.fetchrow(
            f"""
            INSERT INTO personas (user_id, work_id, name, identity, background,
                                  appearance, speech_style, free_note)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING {PERSONA_COLUMNS}
            """,
            user_id,
            work_id,
            name,
            identity,
            background,
            appearance,
            speech_style,
            free_note,
        )
    )


async def update_persona(
    conn: asyncpg.Connection,
    persona_id: int,
    name: str,
    identity: Optional[str] = None,
    background: Optional[str] = None,
    appearance: Optional[str] = None,
    speech_style: Optional[str] = None,
    free_note: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return row_to_dict(
        await conn.fetchrow(
            f"""
            UPDATE personas SET
              name = $2, identity = $3, background = $4, appearance = $5,
              speech_style = $6, free_note = $7, updated_at = now()
            WHERE id = $1
            RETURNING {PERSONA_COLUMNS}
            """,
            persona_id,
            name,
            identity,
            background,
            appearance,
            speech_style,
            free_note,
        )
    )


# ---------------------------------------------------------------------------
# 角色
# ---------------------------------------------------------------------------


CHARACTER_COLUMNS = """
  id, slug, name, aliases, gender, faction, avatar_url, voice_id,
  identity, personality, speech_style, knowledge_scope, bottom_lines,
  taboos, greeting, sample_lines, card_version, is_playable
"""
# 注意：canon_arc（全书轨迹，含剧透）**故意不在这里**。应用的任何读路径都拿不到它，
# 想用它的只有作者与评测脚本，它们自己写 SQL。


async def list_characters(conn: asyncpg.Connection, work_id: int) -> List[Dict[str, Any]]:
    return rows_to_dicts(
        await conn.fetch(
            f"SELECT {CHARACTER_COLUMNS} FROM characters WHERE work_id = $1 ORDER BY id", work_id
        )
    )


async def get_characters_by_slugs(
    conn: asyncpg.Connection, work_id: int, slugs: Sequence[str]
) -> List[Dict[str, Any]]:
    return rows_to_dicts(
        await conn.fetch(
            f"""
            SELECT {CHARACTER_COLUMNS} FROM characters
            WHERE work_id = $1 AND slug = ANY($2::text[])
            """,
            work_id,
            list(slugs),
        )
    )


async def get_characters_by_ids(
    conn: asyncpg.Connection, character_ids: Sequence[int]
) -> List[Dict[str, Any]]:
    if not character_ids:
        return []
    return rows_to_dicts(
        await conn.fetch(
            f"SELECT {CHARACTER_COLUMNS} FROM characters WHERE id = ANY($1::bigint[]) ORDER BY id",
            list(character_ids),
        )
    )


async def get_character_states(
    conn: asyncpg.Connection, anchor_id: int, character_ids: Sequence[int]
) -> Dict[int, Dict[str, Any]]:
    """任意锚点的角色状态，由视图按「最近一次显式状态」填充。"""

    rows = await conn.fetch(
        """
        SELECT character_id, availability, location, status_title, daily_state,
               mood, address_forms, note, state_from_anchor_id
        FROM v_character_states_resolved
        WHERE anchor_id = $1 AND character_id = ANY($2::bigint[])
        """,
        anchor_id,
        list(character_ids),
    )
    return {r["character_id"]: dict(r) for r in rows}


# ---------------------------------------------------------------------------
# 关系
# ---------------------------------------------------------------------------


async def list_effective_relationships(
    conn: asyncpg.Connection,
    persona_id: int,
    work_id: int,
    from_ids: Sequence[int],
    to_ids: Sequence[int],
) -> List[Dict[str, Any]]:
    """按这个身份的当前时间锚点解析后的角色→角色关系（身份覆盖优先）。"""

    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT from_id, to_id, label, private_note, is_known_to_target, effective_source,
                   valid_from_anchor_id, valid_to_anchor_id
            FROM v_effective_relationships
            WHERE persona_id = $1 AND work_id = $2
              AND from_id = ANY($3::bigint[])
              AND to_id = ANY($4::bigint[])
            """,
            persona_id,
            work_id,
            list(from_ids),
            list(to_ids),
        )
    )


async def list_declared_relations(
    conn: asyncpg.Connection,
    persona_id: int,
    character_ids: Sequence[int],
    anchor_seq: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """这个身份与这些角色之间「用户声明」的关系边（用户↔角色，两个方向各一行）。

    F12 的产物：人设语义解析出来后落成这里的边，prompt 按它渲染。
    F14 起按**当前锚点**取生效中的版本——滑回相变之前，旧说法自动重新生效。
    """

    if not character_ids:
        return []
    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT e.from_kind, e.from_id, e.to_kind, e.to_id, e.label, e.private_note,
                   e.is_known_to_target, e.override_scope, e.valid_from_anchor_id,
                   e.valid_to_anchor_id
            FROM relationship_edges e
            LEFT JOIN timeline_anchors vf ON vf.id = e.valid_from_anchor_id
            LEFT JOIN timeline_anchors vt ON vt.id = e.valid_to_anchor_id
            WHERE e.source = 'user'
              AND e.persona_id = $1
              AND (
                (e.from_kind = 'user' AND e.to_kind = 'character' AND e.to_id = ANY($2::bigint[]))
                OR (e.from_kind = 'character' AND e.to_kind = 'user' AND e.from_id = ANY($2::bigint[]))
              )
              AND ($3::int IS NULL OR vf.seq IS NULL OR vf.seq <= $3)
              AND ($3::int IS NULL OR vt.seq IS NULL OR vt.seq > $3)
            ORDER BY e.from_kind, e.from_id, e.id DESC
            """,
            persona_id,
            list(character_ids),
            anchor_seq,
        )
    )


async def version_declared_relation(
    conn: asyncpg.Connection,
    *,
    persona_id: int,
    work_id: int,
    character_id: int,
    direction: str,
    label_after: str,
    anchor_id: int,
    anchor_seq: int,
    reason: Optional[str] = None,
    message_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """把某个方向的声明写成新版本（F14）。

    旧边补 `valid_to` = 当前锚点，新边 `valid_from` = 当前锚点。滑回去旧说法自动生效。
    同时往 relationship_changes 记一条审计，回答「他为什么突然改口认亲」。
    """

    if direction == "user_to_character":
        match = "from_kind = 'user' AND to_kind = 'character' AND to_id = $2"
        insert_kinds = ("user", persona_id, "character", character_id)
    elif direction == "character_to_user":
        match = "from_kind = 'character' AND to_kind = 'user' AND from_id = $2"
        insert_kinds = ("character", character_id, "user", persona_id)
    else:
        raise ValueError(f"未知方向：{direction}")

    async with conn.transaction():
        current = await conn.fetchrow(
            f"""
            SELECT e.id, e.label FROM relationship_edges e
            LEFT JOIN timeline_anchors vf ON vf.id = e.valid_from_anchor_id
            LEFT JOIN timeline_anchors vt ON vt.id = e.valid_to_anchor_id
            WHERE e.source = 'user' AND e.persona_id = $1 AND {match}
              AND (vf.seq IS NULL OR vf.seq <= $3)
              AND (vt.seq IS NULL OR vt.seq > $3)
            ORDER BY e.id DESC LIMIT 1
            """,
            persona_id,
            character_id,
            anchor_seq,
        )
        label_before = current["label"] if current else None
        if label_before == label_after:
            return None  # 说法没变，不写新版本

        if current:
            await conn.execute(
                "UPDATE relationship_edges SET valid_to_anchor_id = $2, updated_at = now() WHERE id = $1",
                current["id"],
                anchor_id,
            )
        # 同一锚点重复写入时，先把这一版的旧记录清掉
        await conn.execute(
            f"""
            DELETE FROM relationship_edges
            WHERE source = 'user' AND persona_id = $1 AND {match} AND valid_from_anchor_id = $3
            """,
            persona_id,
            character_id,
            anchor_id,
        )
        new_id = await conn.fetchval(
            f"""
            INSERT INTO relationship_edges
              (work_id, persona_id, source, from_kind, from_id, to_kind, to_id, label,
               is_known_to_target, override_scope, valid_from_anchor_id)
            VALUES ($1, $2, 'user', $5, $6, $7, $8, $3, true, 'always', $4)
            RETURNING id
            """,
            work_id,
            persona_id,
            label_after,
            anchor_id,
            *insert_kinds,
        )
        await conn.execute(
            """
            INSERT INTO relationship_changes
              (persona_id, user_id, character_id, message_id, anchor_id, direction,
               label_before, label_after, reason)
            SELECT $1::bigint, p.user_id, $2::bigint, $3::bigint, $4::bigint, $5::text,
                   $6::text, $7::text, $8::text
            FROM personas p WHERE p.id = $1::bigint
            """,
            persona_id,
            character_id,
            message_id,
            anchor_id,
            direction,
            label_before,
            label_after,
            reason,
        )
        return {
            "edge_id": new_id,
            "direction": direction,
            "label_before": label_before,
            "label_after": label_after,
            "reason": reason,
        }


async def list_persona_declarations(
    conn: asyncpg.Connection, persona_id: int
) -> List[Dict[str, Any]]:
    """这个身份声明过的关系（用户→角色方向），带角色名，用于界面展示。"""

    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT c.slug AS character, c.name AS character_name, e.label AS user_label
            FROM relationship_edges e
            JOIN characters c ON c.id = e.to_id
            WHERE e.source = 'user' AND e.persona_id = $1
              AND e.from_kind = 'user' AND e.to_kind = 'character'
            ORDER BY c.name
            """,
            persona_id,
        )
    )


async def replace_declared_relation(
    conn: asyncpg.Connection,
    persona_id: int,
    work_id: int,
    character_id: int,
    *,
    user_label: str,
    user_stance: Optional[str],
    character_label: str,
    character_knows: bool,
    override_scope: str = "always",
) -> None:
    """写入一对「用户↔角色」的声明边：用户怎么看他 + 他怎么看她。

    这是**声明动作的产物**：一次声明产出两条有向边（父女本就是双向事实），
    不是「改一边自动同步另一边」——那条硬约束针对的是运行期的隐式同步。
    """

    async with conn.transaction():
        manual = await conn.fetchval(
            """
            SELECT count(*) FROM relationship_edges
            WHERE source = 'user' AND is_manual AND persona_id = $1
              AND (
                (from_kind = 'user' AND to_kind = 'character' AND to_id = $2)
                OR (from_kind = 'character' AND to_kind = 'user' AND from_id = $2)
              )
            """,
            persona_id,
            character_id,
        )
        if manual:
            # 用户手改过的声明优先：重新解析不许覆盖它（见 DECISIONS 2026-09-16）
            return
        await conn.execute(
            """
            DELETE FROM relationship_edges
            WHERE source = 'user' AND persona_id = $1
              AND (
                (from_kind = 'user' AND to_kind = 'character' AND to_id = $2)
                OR (from_kind = 'character' AND to_kind = 'user' AND from_id = $2)
              )
            """,
            persona_id,
            character_id,
        )
        await conn.execute(
            """
            INSERT INTO relationship_edges
              (work_id, persona_id, source, from_kind, from_id, to_kind, to_id, label,
               private_note, is_known_to_target, override_scope)
            VALUES ($1, $2, 'user', 'user', $2, 'character', $3, $4, $6, $7, $8),
                   ($1, $2, 'user', 'character', $3, 'user', $2, $5, NULL, true, $8)
            """,
            work_id,
            persona_id,
            character_id,
            user_label,
            character_label,
            user_stance,
            character_knows,
            override_scope,
        )


async def get_user_character_relation(
    conn: asyncpg.Connection, persona_id: int, character_id: int
) -> Optional[Dict[str, Any]]:
    """角色对这个身份的互动累积（好感度、阶段、里程碑）。"""

    return row_to_dict(
        await conn.fetchrow(
            """
            SELECT milestones, interaction_count, last_interaction_at
            FROM user_character_relations
            WHERE persona_id = $1 AND character_id = $2
            """,
            persona_id,
            character_id,
        )
    )


async def bump_user_character_relation(
    conn: asyncpg.Connection,
    persona_id: int,
    user_id: int,
    character_id: int,
    anchor_id: Optional[int],
) -> None:
    """记一次互动。user_id 是冗余列（复合外键保证与 persona 一致）。"""

    await conn.execute(
        """
        INSERT INTO user_character_relations
          (persona_id, user_id, character_id, interaction_count, first_met_anchor_id,
           last_interaction_at)
        VALUES ($1, $2, $3, 1, $4, now())
        ON CONFLICT (persona_id, character_id) DO UPDATE SET
          interaction_count = user_character_relations.interaction_count + 1,
          first_met_anchor_id = COALESCE(user_character_relations.first_met_anchor_id, EXCLUDED.first_met_anchor_id),
          last_interaction_at = now(),
          updated_at = now()
        """,
        persona_id,
        user_id,
        character_id,
        anchor_id,
    )


# ---------------------------------------------------------------------------
# 会话与消息
# ---------------------------------------------------------------------------


async def create_session(
    conn: asyncpg.Connection,
    persona_id: int,
    work_id: int,
    session_type: str,
    title: str,
    character_ids: Sequence[int],
    created_anchor_id: Optional[int] = None,
    is_non_canon: bool = False,
    scene_id: Optional[int] = None,
) -> Dict[str, Any]:
    """建会话。user_id 从 persona 推出来，免得调用方传错。"""

    user_id = await conn.fetchval("SELECT user_id FROM personas WHERE id = $1", persona_id)
    if user_id is None:
        raise ValueError(f"身份不存在：{persona_id}")

    async with conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO sessions (user_id, persona_id, work_id, session_type, title,
                                  created_anchor_id, scene_id, is_non_canon)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id, user_id, persona_id, work_id, session_type, title, created_anchor_id,
                      pinned_anchor_id, scene_id, is_non_canon, created_at
            """,
            user_id,
            persona_id,
            work_id,
            session_type,
            title,
            created_anchor_id,
            scene_id,
            is_non_canon,
        )
        session = dict(row)
        await conn.execute(
            """
            INSERT INTO session_members (session_id, member_kind, member_id)
            VALUES ($1, 'user', $2)
            """,
            session["id"],
            user_id,
        )
        for character_id in character_ids:
            await conn.execute(
                """
                INSERT INTO session_members (session_id, member_kind, member_id)
                VALUES ($1, 'character', $2)
                ON CONFLICT DO NOTHING
                """,
                session["id"],
                character_id,
            )
    return session


async def get_session(conn: asyncpg.Connection, session_id: int) -> Optional[Dict[str, Any]]:
    return row_to_dict(
        await conn.fetchrow(
            """
            SELECT id, user_id, persona_id, work_id, session_type, title, created_anchor_id,
                   pinned_anchor_id, scene_id, is_non_canon, summary,
                   last_message_at, archived_at
            FROM sessions WHERE id = $1
            """,
            session_id,
        )
    )


async def list_sessions(
    conn: asyncpg.Connection, persona_id: int, work_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """这个身份开过的会话（换身份不该看到另一个身份的历史）。"""

    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT s.id, s.session_type, s.title, s.is_non_canon, s.last_message_at,
                   s.created_at,
                   COALESCE(
                     ARRAY_AGG(c.name ORDER BY c.id) FILTER (WHERE c.id IS NOT NULL),
                     '{}'
                   ) AS member_names
            FROM sessions s
            LEFT JOIN session_members m
                   ON m.session_id = s.id AND m.member_kind = 'character'
            LEFT JOIN characters c ON c.id = m.member_id
            WHERE s.persona_id = $1
              AND ($2::bigint IS NULL OR s.work_id = $2)
              AND s.archived_at IS NULL
            GROUP BY s.id
            ORDER BY COALESCE(s.last_message_at, s.created_at) DESC
            """,
            persona_id,
            work_id,
        )
    )


async def list_session_members(
    conn: asyncpg.Connection, session_id: int
) -> List[Dict[str, Any]]:
    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT member_kind, member_id, is_speaking_enabled
            FROM session_members
            WHERE session_id = $1 AND left_at IS NULL
            ORDER BY member_kind, member_id
            """,
            session_id,
        )
    )


async def list_messages(
    conn: asyncpg.Connection,
    session_id: int,
    limit: int = 50,
    up_to_anchor_seq: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """会话消息，最近的 limit 条。

    `up_to_anchor_seq` 用来实现「记忆按锚点分层」：只取该锚点及之前说过的话，
    之后的不进上下文（往回拨时间线，角色就该忘掉未来）。
    `anchor_id` 为 NULL 的消息（系统消息）视为不随时间变化，始终可见。
    注意：这是**送进模型**的口径；接口原样返回整条会话日志，因为用户自己记得。
    """

    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT id, seq, sender_kind, sender_id, message_kind, content,
                   audio_url, emotion, anchor_id, meta, created_at
            FROM (
              SELECT m.* FROM messages m
              LEFT JOIN timeline_anchors a ON a.id = m.anchor_id
              WHERE m.session_id = $1
                AND ($3::int IS NULL OR a.seq IS NULL OR a.seq <= $3)
              ORDER BY m.seq DESC
              LIMIT $2
            ) t
            ORDER BY seq
            """,
            session_id,
            limit,
            up_to_anchor_seq,
        )
    )


async def lock_session(conn: asyncpg.Connection, session_id: int) -> None:
    """消息 seq 是会话内自增的，插入前先锁住会话行避免并发撞号。"""

    await conn.execute("SELECT id FROM sessions WHERE id = $1 FOR UPDATE", session_id)


async def list_session_messages_at_anchor(
    conn: asyncpg.Connection, session_id: int, anchor_id: int
) -> List[Dict[str, Any]]:
    """这个会话在**这一章**说过的话——摘要的原料（F22）。

    摘要按锚点分段，所以原料也只能取这一段：把别的章节的话混进来，
    摘要就会写成「跨时间点的事」，而角色在那个时间点根本不该知道。
    """

    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT id, seq, sender_kind, sender_id, message_kind, content, created_at
            FROM messages
            WHERE session_id = $1 AND anchor_id = $2
              AND message_kind IN ('text', 'voice', 'narration')
            ORDER BY seq
            """,
            session_id,
            anchor_id,
        )
    )


async def get_anchor_summary(
    conn: asyncpg.Connection, session_id: int, anchor_id: int
) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        """
        SELECT id, session_id, anchor_id, covered_from_seq, covered_to_seq, summary, updated_at
        FROM session_summaries WHERE session_id = $1 AND anchor_id = $2
        """,
        session_id,
        anchor_id,
    )
    return dict(row) if row else None


async def upsert_anchor_summary(
    conn: asyncpg.Connection,
    *,
    session_id: int,
    anchor_id: int,
    covered_from_seq: int,
    covered_to_seq: int,
    summary: str,
) -> Dict[str, Any]:
    """每章一条摘要，随这一章的对话滚动刷新（F22）。"""

    row = await conn.fetchrow(
        """
        INSERT INTO session_summaries
          (session_id, anchor_id, covered_from_seq, covered_to_seq, summary)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (session_id, anchor_id) DO UPDATE
          SET covered_from_seq = EXCLUDED.covered_from_seq,
              covered_to_seq   = EXCLUDED.covered_to_seq,
              summary          = EXCLUDED.summary,
              updated_at       = now()
        RETURNING id, session_id, anchor_id, covered_from_seq, covered_to_seq, summary
        """,
        session_id,
        anchor_id,
        covered_from_seq,
        covered_to_seq,
        summary,
    )
    return dict(row)


async def list_anchor_summaries(
    conn: asyncpg.Connection,
    session_id: int,
    up_to_anchor_seq: int,
    window_start_seq: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """≤ 当前锚点的会话摘要，按时间顺序返回（F22）。

    `window_start_seq` 是「送进 prompt 的最近窗口」的起点：覆盖范围整段落在窗口里的摘要
    不用再给——同一段事说两遍（一遍原文、一遍提要）只会让角色绕圈子。
    只要它**有一段**在窗口之外（`covered_from_seq` 更早），整条摘要就还给。
    """

    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT s.id, s.anchor_id, s.covered_from_seq, s.covered_to_seq, s.summary,
                   a.seq AS anchor_seq, a.chapter_label, a.name AS anchor_name
            FROM session_summaries s
            JOIN timeline_anchors a ON a.id = s.anchor_id
            WHERE s.session_id = $1
              AND a.seq <= $2
              AND ($3::int IS NULL OR s.covered_from_seq < $3)
            ORDER BY a.seq
            """,
            session_id,
            up_to_anchor_seq,
            window_start_seq,
        )
    )


async def append_message(
    conn: asyncpg.Connection,
    session_id: int,
    sender_kind: str,
    sender_id: Optional[int],
    content: str,
    message_kind: str = "text",
    anchor_id: Optional[int] = None,
    model: Optional[str] = None,
    meta: Optional[str] = None,
) -> Dict[str, Any]:
    row = await conn.fetchrow(
        """
        INSERT INTO messages (session_id, seq, sender_kind, sender_id,
                              content, message_kind, anchor_id, model, meta)
        SELECT $1, COALESCE(MAX(seq), 0) + 1, $2, $3, $4, $5, $6, $7, COALESCE($8::jsonb, '{}'::jsonb)
        FROM messages WHERE session_id = $1
        RETURNING id, session_id, seq, sender_kind, sender_id, message_kind, content, created_at
        """,
        session_id,
        sender_kind,
        sender_id,
        content,
        message_kind,
        anchor_id,
        model,
        meta,
    )
    return dict(row)


async def touch_session(conn: asyncpg.Connection, session_id: int) -> None:
    await conn.execute("UPDATE sessions SET last_message_at = now() WHERE id = $1", session_id)


async def merge_message_meta(
    conn: asyncpg.Connection, message_id: int, patch: Dict[str, Any]
) -> None:
    """把一段 JSON 合并进消息的 meta。

    F26 的行动选项就挂在这里（`messages.meta` 本来就是 jsonb），不动 schema。
    """

    await conn.execute(
        "UPDATE messages SET meta = meta || $2::jsonb WHERE id = $1", message_id, patch
    )
