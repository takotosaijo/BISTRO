# PROGRESS.md — 项目状态

状态持久化文件。新会话读完这一份（加上 [AGENTS.md](AGENTS.md)）就应该能接上工作。

**规则**：功能项的状态只能由验证命令的结果驱动。没跑过 `make check`，不许标 `passing`。

新会话接上工作只需要三步：`make check` → 读下面「快照」与「进行中」→ 从「下一步」挑一件。

---

## 快照

- 更新时间：2026-09-15
- 最新提交：本轮三个提交——工程管理骨架 → 接入 DeepSeek → 开发服务端口改 8002；
  工作区干净（提交号看 `git log --oneline -3`）
- 测试：**26/26 通过**（`make test`）
- 数据冒烟测试：**7/7 通过**（`db/tests/verify_seed.sql`）
- 密钥泄漏检查：**通过**（`make secrets`：`.env` 仍被忽略、被追踪文件与提交历史里都没有密钥）
- 数据库：Docker 容器 `mypg`（`pgvector/pgvector:pg16`，vector 0.8.6），`127.0.0.1:5433/bistro`
- 数据量：12 角色 / 12 时间锚点 / 45 状态行 / 24 关系边 / 5 事件 / 19 表 / 3 视图
- 模型层：**已接真实模型 DeepSeek `deepseek-v4-flash`**（`BISTRO_LLM_PROVIDER=deepseek`）；
  `mock` 仍保留，离线可跑通整条链路
- 当前阶段：第 0 期完成，第 1 期未开始

---

## 功能清单

状态取值：`passing`（验证命令已跑通，不可回退）/ `active`（正在做）/ `blocked`（有外部依赖）/ `not_started`

| ID | 行为 | 验证命令 | 状态 |
|---|---|---|---|
| F01 | 数据模型：19 表 3 视图，含时序关系边、状态填充、记忆分层 | `make db-reset` | `passing` |
| F02 | 种子数据：12 角色卡 / 12 时间锚点 / 45 状态行 / 23 关系边 / 5 事件 | `make db-reset` | `passing` |
| F03 | 关系解析：有向、时态、用户覆盖优先于原著 | `db/tests/verify_seed.sql` 第 2–4 项 | `passing` |
| F04 | 角色状态填充：某锚点无显式状态时沿用最近一次 | `db/tests/verify_seed.sql` 第 5–7 项 | `passing` |
| F05 | 1v1 文本对话闭环：装配 → 生成 → 落库 | `pytest -k round_trip` | `passing` |
| F06 | prompt 装配：角色卡 + 锚点 + 状态 + 关系 + 知识边界 | `pytest -k prompt_reflects`；`make run` 后看 `/prompt-preview` | `passing` |
| F07 | SSE 流式输出（meta / delta / done / error 四类事件） | `pytest -k streaming` | `passing` |
| F08 | 角色可用性门控：亡故与未登场角色按时间点拦截 | `pytest -k deceased`、`pytest -k selectable` | `passing` |
| F09 | 拨动时间线后 prompt 与角色处境整体重写 | `pytest -k timeline_advance` | `passing` |
| F10 | 会话隔离：两个会话的消息、上下文、记忆互不串 | `pytest tests/test_session_isolation.py` | `passing` |
| F11 | 记忆层：写入抽取 + 向量检索 + 召回进 prompt | 未实现 | `not_started` |
| F12 | 用户↔角色的显式关系声明（`from_kind='user'`），含 `from_here` 等生效语义 | 未实现 | `not_started` |
| F13 | 角色卡分层：恒定层与时间线层拆开，修掉未来信息泄露 | `pytest -k character_card_layers`（12 角色 × 12 锚点全扫） | `passing` |
| F14 | 好感度衰减与阶段回退，避免"见三次就生死相托" | 未实现 | `not_started` |
| F15 | 群聊发言调度：导演模式 / 自由模式，允许沉默与打断 | 未实现（当前 responder 固定取第一个角色） | `not_started` |
| F16 | 群聊空间合理性校验：场景 + 成员时空校验，不合理可强行开启 | 未实现（`is_non_canon` 字段与 prompt 支持已就绪） | `not_started` |
| F17 | 语音输入：按住说话 → VAD → 流式 ASR | 未实现 | `blocked` |
| F18 | 语音输出：分句流式 TTS，首句延迟 ≤ 1.5 秒，可打断 | 未实现 | `blocked` |
| F19 | 前端舞台页：角色立绘选择、1v1 与群聊入口 | 未实现 | `not_started` |
| F20 | 关系网编辑器：图形化改关系，原著值并排对照 | 未实现 | `not_started` |
| F21 | 角色一致性评测集：每角色 30 条探针题，防 OOC | 未实现 | `not_started` |
| F22 | 会话摘要滚动压缩，替代只取最近 24 条原文 | 未实现 | `not_started` |

F17/F18 标 `blocked` 的原因见下方阻塞项 B02。

---

## 进行中

暂无。F13（角色卡分层）已于 2026-09-15 完成并验证，见下方会话日志。

---

## 已知问题与阻塞

### 阻塞（需要外部条件才能推进）

| ID | 阻塞内容 | 影响 | 解除条件 |
|---|---|---|---|
| ~~B01~~ | ~~未接真实大模型~~ **2026-09-15 已解除** | — | 已接入 DeepSeek `deepseek-v4-flash`，见本文件「会话日志」与 [DECISIONS.md](DECISIONS.md) |
| B02 | 语音供应商未选型（TTS / ASR） | F17、F18 无法开工 | 确定供应商与成本区间 |
| B03 | 未确定目标平台优先级（Web / 小程序 / App） | 影响 F19 的技术选型与工作量 | 确认先做哪个 |

> B01 解除只解决了「有没有真实模型」。**「输出像不像本人」仍未验收**——
> 需要 F21 的角色一致性评测集给结论，在那之前 F06 只能算链路通过。

### 已知问题（不阻塞当前开发）

| ID | 问题 | 现状 |
|---|---|---|
| ~~I01~~ | ~~角色卡 `identity` 是按全书视角写的，早期锚点会泄露未来~~ **2026-09-15 已修复** | 见 F13：恒定层重写、全书轨迹移入 `canon_arc`（永不进 prompt）、关系边招安窗口一并修；12×12 回归测试把关 |
| I02 | 群聊的 responder 固定取第一个角色 | 第 0 期的临时实现，第 2 期换发言调度 |
| I03 | 好感度只增不减 | `user_character_relations` 无衰减规则，长期会失真 |
| I04 | 用户覆盖目前只覆盖角色↔角色 | 用户自己的关系声明还没接进 prompt |
| I05 | 没装 pgvector 的环境会把向量列降级为 jsonb | 仅影响向量检索，本机容器已具备 pgvector |
| I06 | 开发环境是 Python 3.9 | homebrew 的 3.11/3.12 因 libexpat 链接失效不可用；修复命令 `brew reinstall python@3.12` |
| I07 | 数据库容器重启策略是 `no` | Mac 重启后容器不会自动拉起，`make check` 会连不上库 |
| I08 | 市井线三人（潘金莲 / 西门庆 / 王婆）在所有锚点都是 `not_introduced` 或 `deceased` | 首发 12 个角色里有 3 个实际点不开；补一条第二十三~二十四回在阳谷县的状态行即可，属数据补齐 |
| I09 | `sessions.pinned_anchor_id`（把会话固定在某个时间点）管线支持但**没有接口** | 只能直接写库；前端要做「把这个会话钉在第十回」得先补接口，或明确不做 |

---

## 下一步（按优先级）

1. **F21 角色一致性评测集（先做缩水版）**：每角色 5~10 条可机械判定的探针（自称对不对、有没有出戏、
   有没有提前知道未来），先跑 2~3 个角色。F13 已经把「提前知道未来」变成可测项，这套评测是接下来
   改 prompt（F11 / F12）的地网。
2. **F19 最小前端试验台**：单文件 HTML + 章回滑块 + 对话，由 FastAPI 托管，不进构建工具。
   目的是让「拨时间线，人变了」这件事可以用手拨出来给人看——现在只能靠 `/docs` 和 curl 感受。
3. **I08**：给市井线三人补第二十三~二十四回在阳谷县的状态行，让 12 个首发角色都能点开。
4. **F11 记忆层**：pgvector 已就绪，写"对话后抽取记忆 → 向量召回 → 进 prompt"的闭环。
5. **F12 用户↔角色显式关系**：把 `relationship_edges` 的 `from_kind='user'` 接进 prompt。

---

## 会话日志（只追加，最新在下）

### 2026-09-11 · 第 0 期

- 确定产品方案：时间线驱动的世界状态切片，而非剧情回放。产出 `docs/PLAN.md`。
- 数据模型落地：`db/schema.sql`（19 表 3 视图）、`db/seed.sql`（12 角色 / 12 锚点）、`db/tests/verify_seed.sql`（7 项冒烟测试）。
- 后端骨架：FastAPI + asyncpg + 三 Provider 抽象，`app/` 共 11 个模块。
- 第 0 期验收：9 项测试全绿；真实起服务联调，确认第十回与第七十一回的林冲 prompt 完全不同。
- 过程中发现并修复：锚点序号 `seq` 与章回号 `chapter_no` 混淆（接口改为两者都收）；角色卡未来信息泄露（加兜底提示，记入 I01）。

### 2026-09-15 · 环境变更 + 工程管理骨架

- 数据库迁移到 Docker 容器 `mypg`（pgvector/pg16）。因 homebrew PostgreSQL 15 占用 `127.0.0.1:5432`，容器重建为 `5433:5432`，数据卷 `37d4086188b0…` 保留，重建前后数据基线一致（12|12|23|8）。
- 首次在真实 pgvector 上验证 `schema.sql`：vector 0.8.6，两个 hnsw 索引建成，1024 维约束生效。此前"未装 pgvector"的验证缺口关闭。
- 新增工程管理骨架：`AGENTS.md`、`PROGRESS.md`、`DECISIONS.md`、`Makefile`、`db/CONSTRAINTS.md`。
- 未做：上述文件尚未提交（工作区有未提交改动）。

### 2026-09-15 · 接入真实模型 DeepSeek（解 B01）

- 模型层落地：`.env` 配 `BISTRO_LLM_PROVIDER=deepseek`、`BISTRO_LLM_BASE_URL=https://api.deepseek.com/v1`、
  `BISTRO_LLM_MODEL=deepseek-v4-flash`；密钥只写在 `.env`（已被 `.gitignore` 忽略）。
- `app/providers/` 增加 `deepseek` 名字（与 `openai_compat` 共用同一类，只差默认地址），
  provider 名写错改为直接报错，不再静默退回 `mock`。
- 实测 DeepSeek：`/models` 只列 `deepseek-flash` / `deepseek-v4-pro`，`deepseek-v4-flash` 是可用别名
  （响应回落成 `deepseek-flash`）；流式响应分 `reasoning_content`（思考）与 `content`（正文）两个字段，
  解析处只取正文并钉了测试。
- 推理模型的取舍：默认 `BISTRO_LLM_THINKING=auto`，关掉思考（`disabled`）首字延迟实测从 2.54s 降到 1.80s
  （同一句话、第十回林冲，打真实服务）；细节与风险写进 DECISIONS.md。
- 新增验证手段：`scripts/check_llm.py`（真实模型端到端冒烟，支持 `--http` 打真实服务测首字延迟）、
  `scripts/check_secrets.sh` + `make secrets`（密钥泄漏检查，已并入 `make check`）、
  `tests/test_provider_config.py`（7 项，provider 选择 / 默认地址 / thinking 开关 / SSE 解析）。
- 验收：`make check` 通过（测试 16/16、数据冒烟 7/7、密钥检查通过）；
  `scripts/check_llm.py` 真实调用通过，第十回林冲站得住人设
  （「林某是个刺配的配军，往沧州去。掌柜的，这雪夜开门，可有热酒卖？」）。
- 未做：本轮改动尚未提交；工作区另有上一轮的文档改动一并待提交。

### 2026-09-15 · 开发服务端口改 8002 + 在跑的服务上复验

- 端口：8000 被本机另一个项目占用，`make run` 改为默认起在 8002（`PORT ?= 8002`，
  可 `make run PORT=xxxx` 临时覆盖）；`README.md`、`AGENTS.md`、
  `scripts/bootstrap_demo.py`、`scripts/check_llm.py` 里的示例地址一并同步。
- 复验（服务已在 8002 上运行）：`GET /api/health` → `{"status":"ok","llm_provider":"deepseek",
  "llm_model":"deepseek-v4-flash"}`；`GET /docs` → 200；
  `GET /api/sessions/9/prompt-preview` → 第十回林冲的世界状态切片正确；
  `POST /api/sessions/9/messages` → 真实模型回复「林某与你素不相识，问我去向，是何意？」，
  按 prompt 里的「素不相识」关系作答，落库 `provider=deepseek`。
- 本轮 `make check` 仍为 16/16 测试 + 7/7 数据冒烟 + 密钥检查通过。

### 2026-09-15 · F13 角色卡分层：修掉未来信息泄露（解 I01）

- 问题面积：12 张卡里 8 张的 `identity` 按全书视角写（「坐第六把交椅」「被赚上梁山」「毒杀亲夫」），
  `personality` / `knowledge_scope` / `taboos` / `sample_lines` 也夹带未来事实；关系边同样有
  （武松→宋江的心里话从第二十三回就写「招安之事，俺心里不服」）。旧方案只靠 prompt 里一句兜底提示压着，
  而那句提示本身点名了「交椅」——等于先把未来告诉模型。
- 分层：`characters.identity` 及同组字段收窄为**恒定层**（判据：角色第一次登场时是否已经成立），
  新增 `characters.canon_arc` 存全书轨迹，**永不进 prompt**（不在 `CHARACTER_COLUMNS` 里，应用读不到）；
  时序身份照旧只从 `anchor_character_states` 取。兜底提示改为不点名的行为约束。
- 数据修复：12 张卡重写恒定层；锚点6（第二十三回）的「招安之议未起」改为「郓城命案未结，四处缉拿」；
  武松→宋江的关系边拆成两个生效区间（第二十三~五十回不含招安，第七十一回起含）。
- 回归测试：新增 `tests/test_character_card_layers.py`，全扫 12 角色 × 12 锚点——`canon_arc` 不进 prompt、
  早期锚点不出现未来术语、不出现日后才拿到的头衔、不出现「全书/原著」这类出戏词。
  做了反向验证：故意往林冲卡里塞回「第六把交椅」，测试立刻报错，撤回即绿。
- 验收：`make check` 通过（22/22 测试、数据冒烟 7/7、密钥检查通过）；真实模型验证——第十回林冲被问
  「你日后要在梁山上坐第六把交椅，还与高俅同在朝堂受招安，可有此事？」，答
  「这话从何说起？林某眼下连梁山是个甚么去处都不曾见着，哪个与你说的第六把交椅？又提那高俅……
  林某与那厮，只有血海深仇。」
- 数据同步方式：schema 变了但**没有** `make db-reset`（会清掉演示会话）。做法是新建临时库
  `bistro_scratch` 从零构建 schema+seed，再对开发库 `ALTER TABLE characters ADD COLUMN canon_arc`
  并用临时库生成的 UPDATE 覆盖 12 张卡；最后逐字段对比两个库的角色卡 / 锚点世界状态 / 关系边，确认一致
  （关系边 23→24 条）。临时库用完即删。
- 顺带发现：市井线三人在所有锚点都不可对话，记入 I08。

### 2026-09-15 · F10 会话隔离：补上验证（active → passing）

- F10 之前停在 `active`，因为只验证过「两个会话能各建各的」，没验证「不串」。
- 新增 `tests/test_session_isolation.py`，判据是**送进模型的 message 列表**而不是接口返回值：
  1. 同一用户两个会话：历史各自独立，会话内 `seq` 各数各的，另一个会话的消息不许出现；
  2. 两个用户跟同一角色说话：对方的人设与消息都不许进自己的 prompt；
  3. 两个会话**并发**发消息：回复与落库各归各的（回复带各自的此刻处境，落库的 `sender_id` 必须是本会话角色）；
  4. 会话固定在第十回、用户整体拨到第七十一回：固定住的会话不受影响，另一个照常跟着时间线走。
- 变异验证：临时去掉 `list_messages` 的 `session_id` 过滤，4 项立刻变红（失败信息里能直接看到别的会话
  的完整历史被塞进了 prompt），还原即绿。
- 顺带发现（记入 I09）：`sessions.pinned_anchor_id` 在 schema 与对话管线里都支持，但没有接口能设置，
  目前只能直接写库。
- `make check`：26/26 测试 + 数据冒烟 7/7 + 密钥检查通过。
