-- ============================================================================
-- 水浒角色对话系统 · 数据模型
-- PostgreSQL 15+ / pgvector
--
-- 设计要点
--   1. 时间线驱动：所有会随剧情变化的设定（关系、状态、知识）都挂在「锚点」上
--   2. 关系有向且不对称：A→B 与 B→A 是两条独立记录
--   3. 关系分 canon / user 双源，用户源优先，由视图统一解析
--   4. 会话完全隔离：消息、成员、摘要都以 session_id 为边界
--   5. 记忆分三层：会话记忆 / 角色记忆 / 会话摘要
--
-- 向量维度：本文件按 1024 维编写（bge-m3 等中文模型常用维度）
--           若改用 text-embedding-3-small(1536) 等模型，请全局替换 1024
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- 枚举类型
-- ---------------------------------------------------------------------------

-- 参与方类型：角色 或 用户（关系边、会话成员、消息发送者共用）
CREATE TYPE actor_kind AS ENUM ('character', 'user');

-- 会话类型：一对一 / 群聊
CREATE TYPE session_type AS ENUM ('direct', 'group');

-- 关系边来源：原著设定 或 用户覆盖
CREATE TYPE edge_source AS ENUM ('canon', 'user');

-- 用户覆盖的生效语义
CREATE TYPE override_scope AS ENUM ('timeline_point', 'from_here', 'always');

-- 角色在某个时间锚点的可用性
CREATE TYPE character_availability AS ENUM ('introduced', 'not_introduced', 'hidden', 'deceased');

-- 角色对某事件的知情程度（用于派生知识边界）
CREATE TYPE event_knowledge AS ENUM ('witnessed', 'heard', 'rumored', 'unaware');

-- 记忆作用域
CREATE TYPE memory_scope AS ENUM ('session', 'character');

-- 消息类型：普通文本 / 语音 / 系统事件 / 时间线推进 / 旁白
CREATE TYPE message_kind AS ENUM ('text', 'voice', 'system', 'anchor_shift', 'narration');

-- 通用 updated_at 触发器
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 1. 作品与时间线
-- ---------------------------------------------------------------------------

CREATE TABLE works (
  id               bigserial PRIMARY KEY,
  slug             text NOT NULL UNIQUE,
  title            text NOT NULL,
  author           text,
  edition          text,                          -- 版本，如 '100回本'
  total_chapters   int,                           -- 100
  is_public_domain boolean NOT NULL DEFAULT true,
  metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN works.edition IS '同一作品的不同版本关系数据不同，必须锁定一个版本';

CREATE TABLE timeline_anchors (
  id            bigserial PRIMARY KEY,
  work_id       bigint NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  seq           int NOT NULL,               -- 时间顺序，越小越早
  chapter_no    int NOT NULL,               -- UI 显示用的章回号
  chapter_label text NOT NULL,              -- '第十回'
  name          text NOT NULL,              -- '林教头风雪山神庙'
  era_note      text,                       -- 年号 / 季节 / 地点等时间感描述
  world_state   jsonb NOT NULL DEFAULT '{}'::jsonb,
  group_vibe    text,                       -- 群聊氛围，直接进 prompt
  summary       text,
  UNIQUE (work_id, seq),
  UNIQUE (work_id, chapter_no)
);

COMMENT ON TABLE timeline_anchors IS '时间锚点：UI 上呈现为章回滑块，数据上是世界状态切片';
COMMENT ON COLUMN timeline_anchors.world_state IS
  '例如 {"梁山之主":"晁盖","梁山成型":true,"官府态势":"震怒"}';
COMMENT ON COLUMN timeline_anchors.group_vibe IS
  '群聊的默认语气，例如「兄弟相认、商议对付官府」';

-- ---------------------------------------------------------------------------
-- 2. 角色
-- ---------------------------------------------------------------------------

CREATE TABLE characters (
  id              bigserial PRIMARY KEY,
  work_id         bigint NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  slug            text NOT NULL,
  name            text NOT NULL,
  aliases         text[] NOT NULL DEFAULT '{}',   -- 绰号、别称，如 '豹子头'
  gender          text,
  faction         text,                           -- '梁山' / '市井' / '官府'
  avatar_url      text,
  voice_id        text,                           -- TTS 音色标识
  -- ---- 恒定层：任何时间锚点都必须成立，会直接进 prompt ----
  -- 判据：这句话在角色第一次登场时是否就已经是真的？不是，就放 canon_arc 或锚点状态里。
  identity        text NOT NULL,                  -- 出身与本事，不含剧情走向
  personality     text NOT NULL,
  speech_style    text NOT NULL,                  -- 语言风格、口头禅、称呼习惯
  knowledge_scope text,                           -- 知识边界的额外说明
  bottom_lines    text,                           -- 底线：防 OOC 与防恋爱线失控
  taboos          text,
  greeting        text,                           -- 1v1 开场白
  sample_lines    jsonb NOT NULL DEFAULT '[]'::jsonb,  -- few-shot 典范台词
  card_version    int NOT NULL DEFAULT 1,
  is_playable     boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),

  -- 全书轨迹（含剧透）：只给作者与评测看，**禁止进 prompt**。
  -- 以前这段内容写在 identity 里，于是第二回的林冲就知道自己将来会坐第六把交椅（I01）。
  canon_arc       text,

  UNIQUE (work_id, slug)
);

CREATE TRIGGER characters_updated_at
  BEFORE UPDATE ON characters
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX characters_aliases_idx ON characters USING gin (aliases);

-- 原著知识库：带生效锚点，用于 RAG 并防止剧透
CREATE TABLE character_lore_chunks (
  id                   bigserial PRIMARY KEY,
  work_id              bigint NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  character_id         bigint REFERENCES characters(id) ON DELETE CASCADE,  -- NULL = 通用背景
  valid_from_anchor_id bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,
  valid_to_anchor_id   bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,
  chapter_no           int,
  content              text NOT NULL,
  tags                 text[] NOT NULL DEFAULT '{}',
  embedding            vector(1024),
  created_at           timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE character_lore_chunks IS
  '按 current_anchor 过滤后再做向量检索，同一个 block 才不会知道未来的事';

CREATE INDEX character_lore_chunks_scope_idx
  ON character_lore_chunks (work_id, character_id, valid_from_anchor_id);
CREATE INDEX character_lore_chunks_embedding_idx
  ON character_lore_chunks USING hnsw (embedding vector_cosine_ops);

-- 每个角色在每个锚点的状态：回答「最近在干什么」的依据
CREATE TABLE anchor_character_states (
  anchor_id     bigint NOT NULL REFERENCES timeline_anchors(id) ON DELETE CASCADE,
  character_id  bigint NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  availability  character_availability NOT NULL DEFAULT 'introduced',
  location      text,
  status_title  text,        -- 此刻身份，如 '梁山泊第四把交椅'
  daily_state   text,        -- 回答「最近在干什么」
  mood          text,
  address_forms jsonb NOT NULL DEFAULT '{}'::jsonb,  -- 对不同人的称呼
  note          text,
  PRIMARY KEY (anchor_id, character_id)
);

COMMENT ON COLUMN anchor_character_states.daily_state IS
  '必须人工校准。纯靠模型生成会出现与剧情阶段不符的状态';
COMMENT ON COLUMN anchor_character_states.address_forms IS
  '例如 {"宋江":"哥哥","林冲":"林教头"}，随时间推进而变化';

-- ---------------------------------------------------------------------------
-- 3. 事件与知识边界
-- ---------------------------------------------------------------------------

CREATE TABLE story_events (
  id              bigserial PRIMARY KEY,
  work_id         bigint NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  anchor_id       bigint NOT NULL REFERENCES timeline_anchors(id) ON DELETE CASCADE,
  name            text NOT NULL,
  summary         text NOT NULL,
  is_public_rumor boolean NOT NULL DEFAULT false,   -- 是否天下皆知
  UNIQUE (work_id, anchor_id, name)
);

CREATE TABLE event_witnesses (
  event_id     bigint NOT NULL REFERENCES story_events(id) ON DELETE CASCADE,
  character_id bigint NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  knowledge    event_knowledge NOT NULL DEFAULT 'witnessed',
  PRIMARY KEY (event_id, character_id)
);

COMMENT ON TABLE event_witnesses IS
  '角色只知道 seq <= 当前锚点、且自己是 witnessed/heard 的事件';

-- ---------------------------------------------------------------------------
-- 4. 用户与用户设定
-- ---------------------------------------------------------------------------

CREATE TABLE users (
  id          bigserial PRIMARY KEY,
  external_id text UNIQUE,
  display_name text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- 身份（persona）：一个账号在一个作品里可以有多个人设（玉娆 / 苏娘 / 路人张三）。
-- 关系、会话、记忆都挂在身份上——换身份就是换了一个人，历史不该共用。
-- 时间线（拨到第几回）留在账号层 `user_timeline_settings`：世界时间是同一个。
--
-- 2026-09-16 由「一个账号一个人设」（旧表 user_personas）升级而来，见 DECISIONS。
CREATE TABLE personas (
  id           bigserial PRIMARY KEY,
  user_id      bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  work_id      bigint NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  name         text NOT NULL,
  identity     text,        -- 我在这个世界里的身份
  background   text,
  appearance   text,
  speech_style text,
  free_note    text,
  is_archived  boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  -- 复合外键的靶子：让 sessions / relations / memories 能同时钉住身份与它所属的账号，
  -- 从结构上杜绝「身份属于 A、记录却记在 B 名下」这种漂移
  UNIQUE (id, user_id)
);

CREATE INDEX personas_user_work_idx ON personas (user_id, work_id) WHERE NOT is_archived;

CREATE TABLE user_timeline_settings (
  user_id               bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  work_id               bigint NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  current_anchor_id     bigint NOT NULL REFERENCES timeline_anchors(id),
  canon_lock            boolean NOT NULL DEFAULT true,   -- true = 正史模式
  allow_early_characters boolean NOT NULL DEFAULT false, -- 是否允许唤醒未登场角色
  spoiler_guard         boolean NOT NULL DEFAULT true,   -- 是否禁止角色引用未来
  updated_at            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, work_id)
);

-- ---------------------------------------------------------------------------
-- 5. 关系图（有向边，canon / user 双源）
-- ---------------------------------------------------------------------------

CREATE TABLE relationship_edges (
  id            bigserial PRIMARY KEY,
  work_id       bigint NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  -- source='user' 时指向声明这条关系的**身份**（不是账号）：换身份就是换一套关系。
  -- 用户↔角色的两个方向各占一行，两行的 persona_id 都填同一个身份，方向由 from/to 表达。
  persona_id    bigint REFERENCES personas(id) ON DELETE CASCADE,
  source        edge_source NOT NULL,
  from_kind     actor_kind NOT NULL,
  from_id       bigint NOT NULL,
  to_kind       actor_kind NOT NULL,
  to_id         bigint NOT NULL,
  -- 关系强度不用数值表达（2026-09-16 取消好感度）：说什么关系，全看 label 与生效区间。
  -- 变化 = 插一条新边（valid_from=变化发生的锚点），旧边补 valid_to——滑回去旧说法自动生效。
  label         text NOT NULL,          -- '结义兄弟' / '血仇' / '我认下的女儿'
  private_note  text,                   -- 只有 from 自己知道的内心话
  is_known_to_target boolean NOT NULL DEFAULT true,  -- to 是否意识到这层关系
  valid_from_anchor_id bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,  -- NULL = 自始
  valid_to_anchor_id   bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,  -- NULL = 至今后
  override_scope override_scope,        -- 仅 source='user' 有值
  is_manual     boolean NOT NULL DEFAULT false,  -- 用户手改过的声明：重新解析时不许覆盖
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (source = 'user' OR persona_id IS NULL),
  CHECK (source <> 'user' OR persona_id IS NOT NULL),
  CHECK (source <> 'user' OR override_scope IS NOT NULL),
  -- 角色不能对自己成边（身份与角色的 id 可能同值，故按 kind 判定）
  CHECK (NOT (from_kind = 'character' AND to_kind = 'character' AND from_id = to_id))
);

COMMENT ON TABLE relationship_edges IS
  '有向关系边。A→B 与 B→A 独立，允许不对称（A 恨 B 但 B 爱 A）';
COMMENT ON COLUMN relationship_edges.private_note IS
  '仅注入 from 的 prompt，不注入 to 的 prompt，是「内心戏」的来源';
COMMENT ON COLUMN relationship_edges.valid_from_anchor_id IS
  'NULL 表示自始生效。canon 边一般都有明确起始锚点';
COMMENT ON COLUMN relationship_edges.override_scope IS
  'timeline_point=仅当前锚点；from_here=从当前锚点起；always=全时段覆盖';

CREATE TRIGGER relationship_edges_updated_at
  BEFORE UPDATE ON relationship_edges
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 原著边：同一个 (from, to) 在同一锚点只能有一条
CREATE UNIQUE INDEX relationship_edges_canon_uniq
  ON relationship_edges (work_id, from_kind, from_id, to_kind, to_id, valid_from_anchor_id)
  WHERE source = 'canon';

-- 用户边：允许同一对关系在时间线上分段覆盖（第2回结义、第40回反目）
CREATE UNIQUE INDEX relationship_edges_persona_uniq
  ON relationship_edges (persona_id, from_kind, from_id, to_kind, to_id, (COALESCE(valid_from_anchor_id, 0)))
  WHERE source = 'user';

CREATE INDEX relationship_edges_from_idx
  ON relationship_edges (work_id, from_kind, from_id);
CREATE INDEX relationship_edges_to_idx
  ON relationship_edges (work_id, to_kind, to_id);
CREATE INDEX relationship_edges_persona_idx
  ON relationship_edges (persona_id) WHERE source = 'user';

-- ---------------------------------------------------------------------------
-- 6. 用户 ↔ 角色 的互动累积（跨时间线延续）
-- ---------------------------------------------------------------------------

CREATE TABLE user_character_relations (
  persona_id       bigint NOT NULL,
  user_id          bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- 冗余但被复合外键锁死
  character_id     bigint NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  -- 这张表只记「经历过什么」，不记「多亲近」：好感度与恋爱线已取消（2026-09-16）。
  -- 「现在是什么关系」由 relationship_edges 的边版本表达。
  first_met_anchor_id bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,
  milestones       jsonb NOT NULL DEFAULT '[]'::jsonb,   -- 已发生的里程碑（相认 / 结拜 / 翻脸…）
  interaction_count int NOT NULL DEFAULT 0,
  last_interaction_at timestamptz,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (persona_id, character_id),
  -- DEFERRABLE：允许在一个事务里同时搬迁 personas.user_id 与引用它的行（合并身份时用）
  FOREIGN KEY (persona_id, user_id) REFERENCES personas(id, user_id)
    ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
);

COMMENT ON TABLE user_character_relations IS
  '互动累积层，不随时间线重置。这是「他记得你」的来源';

CREATE TRIGGER user_character_relations_updated_at
  BEFORE UPDATE ON user_character_relations
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 关系变化审计：可解释「他为什么突然冷淡了」
CREATE TABLE relationship_changes (
  id            bigserial PRIMARY KEY,
  persona_id    bigint NOT NULL,
  user_id       bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  character_id  bigint NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  message_id    bigint,        -- 触发变化的消息（FK 在 messages 建表后补）
  anchor_id     bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,  -- 变化发生在哪一回
  direction     text NOT NULL CHECK (direction IN ('user_to_character', 'character_to_user')),
  label_before  text,
  label_after   text NOT NULL,
  reason        text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (persona_id, user_id) REFERENCES personas(id, user_id)
    ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
);

CREATE INDEX relationship_changes_user_idx
  ON relationship_changes (user_id, character_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- 7. 场景与会话
-- ---------------------------------------------------------------------------

CREATE TABLE scenes (
  id          bigserial PRIMARY KEY,
  work_id     bigint NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  anchor_id   bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,
  name        text NOT NULL,
  location    text NOT NULL,
  description text,
  is_canon    boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
  id                bigserial PRIMARY KEY,
  user_id           bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  persona_id        bigint NOT NULL,          -- 这个会话是「以哪个身份」开的
  work_id           bigint NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  session_type      session_type NOT NULL,
  title             text NOT NULL,
  created_anchor_id bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,
  pinned_anchor_id  bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,  -- 把会话固定在某时间点
  scene_id          bigint REFERENCES scenes(id) ON DELETE SET NULL,
  is_non_canon      boolean NOT NULL DEFAULT false,   -- 强行开启的不合理会话
  summary           text,
  last_message_at   timestamptz,
  archived_at       timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (persona_id, user_id) REFERENCES personas(id, user_id)
    ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
);

COMMENT ON COLUMN sessions.pinned_anchor_id IS
  'NULL = 跟随用户当前时间线；有值 = 该会话固定在某个时间点';
COMMENT ON COLUMN sessions.is_non_canon IS
  'true 时会把「这是一次不太可能的相遇」写进 prompt';

CREATE INDEX sessions_user_idx
  ON sessions (user_id, work_id, last_message_at DESC NULLS LAST);

CREATE TABLE session_members (
  session_id         bigint NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  member_kind        actor_kind NOT NULL,
  member_id          bigint NOT NULL,
  is_speaking_enabled boolean NOT NULL DEFAULT true,   -- 调度器是否允许该成员发言
  joined_at          timestamptz NOT NULL DEFAULT now(),
  left_at            timestamptz,
  PRIMARY KEY (session_id, member_kind, member_id)
);

COMMENT ON COLUMN session_members.is_speaking_enabled IS
  '用户可以让某个角色「先别说话」，避免群聊里全员抢话';

-- ---------------------------------------------------------------------------
-- 8. 消息
-- ---------------------------------------------------------------------------

CREATE TABLE messages (
  id               bigserial PRIMARY KEY,
  session_id       bigint NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq              int NOT NULL,               -- 会话内单调递增
  sender_kind      actor_kind NOT NULL,
  sender_id        bigint,                     -- 系统消息可为空
  message_kind     message_kind NOT NULL DEFAULT 'text',
  content          text NOT NULL,
  audio_url        text,
  audio_duration_ms int,
  asr_confidence   real,
  emotion          text,
  reply_to_id      bigint REFERENCES messages(id) ON DELETE SET NULL,
  anchor_id        bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,  -- 该条消息发生的时间点
  model            text,
  prompt_tokens    int,
  completion_tokens int,
  meta             jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, seq)
);

COMMENT ON COLUMN messages.anchor_id IS
  '逐条记录时间点，时间线推进后仍能还原「这句话是什么时候说的」';

CREATE INDEX messages_session_idx ON messages (session_id, seq);
CREATE INDEX messages_sender_idx ON messages (sender_kind, sender_id, created_at DESC);

ALTER TABLE relationship_changes
  ADD CONSTRAINT relationship_changes_message_fk
  FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- 9. 记忆
-- ---------------------------------------------------------------------------

CREATE TABLE memories (
  id                bigserial PRIMARY KEY,
  user_id           bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  persona_id        bigint NOT NULL,          -- 记忆属于身份，不属于账号
  work_id           bigint NOT NULL REFERENCES works(id) ON DELETE CASCADE,
  scope             memory_scope NOT NULL,
  session_id        bigint REFERENCES sessions(id) ON DELETE CASCADE,     -- scope='session'
  character_id      bigint REFERENCES characters(id) ON DELETE CASCADE,  -- scope='character' 记忆的持有者
  about_kind        actor_kind,          -- 这条记忆关于谁
  about_id          bigint,
  content           text NOT NULL,
  embedding         vector(1024),
  salience          smallint NOT NULL DEFAULT 50 CHECK (salience BETWEEN 0 AND 100),
  source_message_id bigint REFERENCES messages(id) ON DELETE SET NULL,
  learned_at_anchor_id bigint REFERENCES timeline_anchors(id) ON DELETE SET NULL,
                                        -- 这条记忆是「在哪一章」知道的（F22 起记忆也按锚点分层）
  last_recalled_at  timestamptz,
  recall_count      int NOT NULL DEFAULT 0,
  created_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (scope <> 'session'   OR session_id   IS NOT NULL),
  CHECK (scope <> 'character' OR character_id IS NOT NULL),
  FOREIGN KEY (persona_id, user_id) REFERENCES personas(id, user_id)
    ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE
);

COMMENT ON TABLE memories IS
  'scope=session 仅本窗口可见；scope=character 为角色×用户，跨窗口';

CREATE INDEX memories_scope_idx
  ON memories (user_id, scope, character_id, session_id);
CREATE INDEX memories_embedding_idx
  ON memories USING hnsw (embedding vector_cosine_ops);

CREATE TABLE session_summaries (
  id               bigserial PRIMARY KEY,
  session_id       bigint NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  anchor_id        bigint NOT NULL REFERENCES timeline_anchors(id) ON DELETE CASCADE,
  covered_from_seq int NOT NULL,      -- 这一段从会话内第几条消息起
  covered_to_seq   int NOT NULL,      -- 覆盖到第几条
  summary          text NOT NULL,
  embedding        vector(1024),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, anchor_id)      -- 每章一条，随这一章的对话滚动刷新
);

COMMENT ON TABLE session_summaries IS
  '会话摘要按**时间锚点**分段：每章一条（F22）。prompt 只取覆盖范围落在最近窗口之外的那些当'
  '「前情提要」——原话还在窗口里的部分不再摘要一遍，免得同一段事被说两遍。';
COMMENT ON COLUMN session_summaries.covered_from_seq IS
  '摘要覆盖范围的下界。判断「这段摘要有没有原话还在窗口里」要用它，'
  '只有 covered_to_seq 的话，窗口跨过章节边界时会算错。';

-- ---------------------------------------------------------------------------
-- 10. 关系解析视图：用户覆盖优先，并受时间线约束
-- ---------------------------------------------------------------------------

CREATE VIEW v_effective_relationships AS
WITH base AS (
  -- 关系按**身份**解析：时间线仍取账号的当前锚点，但用户覆盖只认这个身份自己声明的
  SELECT p.id AS persona_id,
         p.user_id,
         ts.work_id,
         a.id  AS anchor_id,
         a.seq AS anchor_seq
  FROM personas p
  JOIN user_timeline_settings ts
    ON ts.user_id = p.user_id AND ts.work_id = p.work_id
  JOIN timeline_anchors a ON a.id = ts.current_anchor_id
),
candidates AS (
  SELECT b.persona_id,
         b.user_id,
         b.work_id,
         b.anchor_id,
         e.from_kind,
         e.from_id,
         e.to_kind,
         e.to_id,
         e.label,
         e.private_note,
         e.is_known_to_target,
         e.source,
         e.override_scope,
         e.valid_from_anchor_id,
         e.valid_to_anchor_id,
         CASE WHEN e.source = 'user' THEN 1 ELSE 0 END AS priority,
         COALESCE(vf.seq, -1) AS from_seq,
         vt.seq               AS to_seq
  FROM base b
  JOIN relationship_edges e
    ON e.work_id = b.work_id
   AND (e.persona_id = b.persona_id OR e.persona_id IS NULL)
   AND e.from_kind = 'character'      -- 角色↔角色；用户相关的边单独渲染
   AND e.to_kind   = 'character'
  LEFT JOIN timeline_anchors vf ON vf.id = e.valid_from_anchor_id
  LEFT JOIN timeline_anchors vt ON vt.id = e.valid_to_anchor_id
  WHERE COALESCE(vf.seq, -1) <= b.anchor_seq
    AND (vt.seq IS NULL OR vt.seq > b.anchor_seq)
)
SELECT DISTINCT ON (persona_id, from_kind, from_id, to_kind, to_id)
       persona_id, user_id, work_id, anchor_id,
       from_kind, from_id, to_kind, to_id,
       label, private_note, is_known_to_target,
       source AS effective_source, override_scope,
       valid_from_anchor_id, valid_to_anchor_id
FROM candidates
ORDER BY persona_id, from_kind, from_id, to_kind, to_id,
         priority DESC,      -- 用户覆盖优先
         from_seq DESC;      -- 同一来源取最近生效的一条

COMMENT ON VIEW v_effective_relationships IS
  '按用户当前时间锚点解析出的角色↔角色关系。用户覆盖优先于原著，且受生效区间约束';

-- 会话成员 + 生效关系，便于装配 prompt
CREATE VIEW v_session_relationship_context AS
SELECT s.id AS session_id,
       s.user_id,
       s.persona_id,
       r.from_id AS from_character_id,
       r.to_id   AS to_character_id,
       r.label,
       r.private_note,
       r.is_known_to_target,
       r.effective_source
FROM sessions s
JOIN v_effective_relationships r
  ON r.persona_id = s.persona_id AND r.work_id = s.work_id
JOIN session_members mf ON mf.session_id = s.id
                       AND mf.member_kind = 'character' AND mf.member_id = r.from_id
JOIN session_members mt ON mt.session_id = s.id
                       AND mt.member_kind = 'character' AND mt.member_id = r.to_id
WHERE s.archived_at IS NULL;

COMMENT ON VIEW v_session_relationship_context IS
  '只暴露当前会话参与者之间的关系，避免把整张关系图塞进 prompt';

-- 角色状态支持「填充语义」：某锚点没有显式状态时，沿用最近一次已定义的状态。
-- 这样只需在状态真正发生变化时写一行，不必维护 角色数 × 锚点数 的完整矩阵。
CREATE VIEW v_character_states_resolved AS
SELECT DISTINCT ON (cur.id, prev.character_id)
       cur.id AS anchor_id,
       cur.work_id,
       prev.character_id,
       prev.anchor_id AS state_from_anchor_id,
       prev.availability,
       prev.location,
       prev.status_title,
       prev.daily_state,
       prev.mood,
       prev.address_forms,
       prev.note
FROM timeline_anchors cur
JOIN timeline_anchors past
  ON past.work_id = cur.work_id
 AND past.seq <= cur.seq
JOIN anchor_character_states prev
  ON prev.anchor_id = past.id
ORDER BY cur.id, prev.character_id, past.seq DESC;

COMMENT ON VIEW v_character_states_resolved IS
  '任意锚点上的角色状态，按最近的显式状态填充。没有历史状态的角色不会出现在结果里，应用层应回退为未登场';

COMMIT;
