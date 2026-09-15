# db/ 目录的硬约束

改这个目录之前先读这份。数据模型是产品的地基，这里改错的代价最高。

## 唯一事实来源

`schema.sql` 是数据模型的唯一事实来源。应用层**不要**再定义一套模型，
也不要重写关系与状态的解析逻辑——那两件事已经固化成视图：

| 视图 | 作用 |
|---|---|
| `v_effective_relationships` | 按用户当前锚点解析角色↔角色关系，用户覆盖优先于原著 |
| `v_character_states_resolved` | 任意锚点的角色状态，按最近一次显式状态填充 |
| `v_session_relationship_context` | 只暴露当前会话参与者之间的关系，避免把整张关系图塞进 prompt |

## 禁止事项

1. 不要用 `docker rm -v` 删容器——会连数据卷一起删掉。
2. 不要为了让流程变绿而删除或放宽 `tests/verify_seed.sql` 里的断言。
3. 不要把 `valid_from_anchor_id` / `valid_to_anchor_id` 换成单一时间点字段，
   关系的时态是这个产品的核心能力。
4. 不要让关系边变成无向或"双向自动同步"。
5. 不要改动作品版本设定（100 回本）与人物结局。
6. 不要按 120 回逐回建锚点，只在状态真正变化处切锚点。

## 改动同步表

| 改了什么 | 必须同时改 |
|---|---|
| 表结构 | `db/tests/verify_seed.sql` 的相关断言、`app/repository.py` 的 SQL |
| 视图语义 | `app/prompt.py` 的渲染逻辑、`tests/test_chat_flow.py` |
| 枚举值 | `app/prompt.py` 里的中文映射表（如 `STAGE_LABELS`） |
| 向量维度 1024 | 全局替换，`schema.sql` 头部有说明；同时确认召回侧维度一致 |

## 容易踩的坑

- **状态填充的副作用**：某锚点没写显式状态时会沿用最近一次的状态。忘了写新状态，
  角色就会"停滞"在旧处境（第 0 期林冲从第十回卡到第七十一回就是这么发现的）。
  改动锚点后抽查：`SELECT * FROM v_character_states_resolved WHERE character_id = ...`
- **pgvector 是否存在会影响建表路径**：`scripts/dev_db.sh` 会检测并自动降级为 jsonb。
  降级后可以开发，但无法验证向量检索。
- **枚举是字符类型**：从 psql 里看是字符串，拼错不会立刻报错，会静默渲染成默认文案。
