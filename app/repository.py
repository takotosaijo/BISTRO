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


async def get_persona(
    conn: asyncpg.Connection, user_id: int, work_id: int
) -> Optional[Dict[str, Any]]:
    return row_to_dict(
        await conn.fetchrow(
            """
            SELECT name, identity, background, appearance, speech_style, free_note
            FROM user_personas WHERE user_id = $1 AND work_id = $2
            """,
            user_id,
            work_id,
        )
    )


async def upsert_persona(
    conn: asyncpg.Connection,
    user_id: int,
    work_id: int,
    name: str,
    identity: Optional[str] = None,
    background: Optional[str] = None,
    appearance: Optional[str] = None,
    speech_style: Optional[str] = None,
    free_note: Optional[str] = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO user_personas (user_id, work_id, name, identity, background,
                                   appearance, speech_style, free_note)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (user_id, work_id) DO UPDATE SET
          name = EXCLUDED.name,
          identity = EXCLUDED.identity,
          background = EXCLUDED.background,
          appearance = EXCLUDED.appearance,
          speech_style = EXCLUDED.speech_style,
          free_note = EXCLUDED.free_note,
          updated_at = now()
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


# ---------------------------------------------------------------------------
# 角色
# ---------------------------------------------------------------------------


CHARACTER_COLUMNS = """
  id, slug, name, aliases, gender, faction, avatar_url, voice_id,
  identity, personality, speech_style, knowledge_scope, bottom_lines,
  taboos, greeting, sample_lines, card_version, is_playable
"""


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
    user_id: int,
    work_id: int,
    from_ids: Sequence[int],
    to_ids: Sequence[int],
) -> List[Dict[str, Any]]:
    """按用户当前时间锚点解析后的角色→角色关系（用户覆盖优先）。"""

    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT from_id, to_id, label, closeness, trust, wariness, affection,
                   private_note, is_known_to_target, effective_source,
                   valid_from_anchor_id, valid_to_anchor_id
            FROM v_effective_relationships
            WHERE user_id = $1 AND work_id = $2
              AND from_id = ANY($3::bigint[])
              AND to_id = ANY($4::bigint[])
            """,
            user_id,
            work_id,
            list(from_ids),
            list(to_ids),
        )
    )


async def get_user_character_relation(
    conn: asyncpg.Connection, user_id: int, character_id: int
) -> Optional[Dict[str, Any]]:
    """角色对用户的互动累积（好感度、阶段、里程碑），跨时间线延续。"""

    return row_to_dict(
        await conn.fetchrow(
            """
            SELECT stage, char_affinity, char_trust, char_wariness, user_stance,
                   milestones, interaction_count, last_interaction_at
            FROM user_character_relations
            WHERE user_id = $1 AND character_id = $2
            """,
            user_id,
            character_id,
        )
    )


async def bump_user_character_relation(
    conn: asyncpg.Connection,
    user_id: int,
    character_id: int,
    anchor_id: Optional[int],
) -> None:
    await conn.execute(
        """
        INSERT INTO user_character_relations
          (user_id, character_id, interaction_count, first_met_anchor_id, last_interaction_at)
        VALUES ($1, $2, 1, $3, now())
        ON CONFLICT (user_id, character_id) DO UPDATE SET
          interaction_count = user_character_relations.interaction_count + 1,
          first_met_anchor_id = COALESCE(user_character_relations.first_met_anchor_id, EXCLUDED.first_met_anchor_id),
          last_interaction_at = now(),
          updated_at = now()
        """,
        user_id,
        character_id,
        anchor_id,
    )


# ---------------------------------------------------------------------------
# 会话与消息
# ---------------------------------------------------------------------------


async def create_session(
    conn: asyncpg.Connection,
    user_id: int,
    work_id: int,
    session_type: str,
    title: str,
    character_ids: Sequence[int],
    created_anchor_id: Optional[int] = None,
    is_non_canon: bool = False,
    scene_id: Optional[int] = None,
) -> Dict[str, Any]:
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO sessions (user_id, work_id, session_type, title,
                                  created_anchor_id, scene_id, is_non_canon)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, user_id, work_id, session_type, title, created_anchor_id,
                      pinned_anchor_id, scene_id, is_non_canon, created_at
            """,
            user_id,
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
            SELECT id, user_id, work_id, session_type, title, created_anchor_id,
                   pinned_anchor_id, scene_id, is_non_canon, summary,
                   last_message_at, archived_at
            FROM sessions WHERE id = $1
            """,
            session_id,
        )
    )


async def list_sessions(
    conn: asyncpg.Connection, user_id: int, work_id: Optional[int] = None
) -> List[Dict[str, Any]]:
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
            WHERE s.user_id = $1
              AND ($2::bigint IS NULL OR s.work_id = $2)
              AND s.archived_at IS NULL
            GROUP BY s.id
            ORDER BY COALESCE(s.last_message_at, s.created_at) DESC
            """,
            user_id,
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
    conn: asyncpg.Connection, session_id: int, limit: int = 50
) -> List[Dict[str, Any]]:
    return rows_to_dicts(
        await conn.fetch(
            """
            SELECT id, seq, sender_kind, sender_id, message_kind, content,
                   audio_url, emotion, anchor_id, created_at
            FROM (
              SELECT * FROM messages WHERE session_id = $1 ORDER BY seq DESC LIMIT $2
            ) t
            ORDER BY seq
            """,
            session_id,
            limit,
        )
    )


async def lock_session(conn: asyncpg.Connection, session_id: int) -> None:
    """消息 seq 是会话内自增的，插入前先锁住会话行避免并发撞号。"""

    await conn.execute("SELECT id FROM sessions WHERE id = $1 FOR UPDATE", session_id)


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
