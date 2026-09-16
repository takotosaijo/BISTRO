# PROGRESS.md — 项目状态

状态持久化文件。新会话读完这一份（加上 [AGENTS.md](AGENTS.md)）就应该能接上工作。

**规则**：功能项的状态只能由验证命令的结果驱动。没跑过 `make check`，不许标 `passing`。

新会话接上工作只需要三步：`make check` → 读下面「快照」与「进行中」→ 从「下一步」挑一件。

---

## 快照

- 更新时间：2026-09-15
- 最新提交：本轮三个提交——工程管理骨架 → 接入 DeepSeek → 开发服务端口改 8002；
  工作区干净（提交号看 `git log --oneline -3`）
- 测试：**34/34 通过**（`make test`）
- 数据冒烟测试：**7/7 通过**（`db/tests/verify_seed.sql`）
- 密钥泄漏检查：**通过**（`make secrets`：`.env` 仍被忽略、被追踪文件与提交历史里都没有密钥）
- 角色评测：**13/13 通过**（`make eval`；林冲 / 武松 / 高俅 共 13 条探针，判据见 `scripts/eval_characters.py`）
- 数据库：Docker 容器 `mypg`（`pgvector/pgvector:pg16`，vector 0.8.6），`127.0.0.1:5433/bistro`
- 数据量：12 角色 / 12 时间锚点 / 45 状态行 / 24 关系边 / 5 事件 / 19 表 / 3 视图
- 模型层：**已接真实模型 DeepSeek `deepseek-v4-flash`**（`BISTRO_LLM_PROVIDER=deepseek`）；
  `mock` 仍保留，离线可跑通整条链路
- 形态：`make run` 后 <http://127.0.0.1:8002/> 是**试验台页面**（F19 缩水版，单文件 HTML）
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
| F11 | 记忆层：写入抽取 + 向量检索 + 召回进 prompt（**按锚点分层**：只召回 ≤ 当前锚点的记忆） | 未实现 | `not_started` |
| F12 | 用户↔角色关系：由**人设语义解析**产出结构化关系（类型 / 方向 / 强度 / 生效范围），可手改、手改优先 | 未实现（2026-09-16 重定义，见 DECISIONS） | `not_started` |
| F13 | 角色卡分层：恒定层与时间线层拆开，修掉未来信息泄露 | `pytest -k character_card_layers`（12 角色 × 12 锚点全扫） | `passing` |
| F14 | 关系演化：互动后按阈值推进阶段，并**按锚点分层可回退**（滑回过去，关系也回到那一刻） | 未实现 | `not_started` |
| F15 | 群聊发言调度：导演模式 / 自由模式，允许沉默与打断 | 未实现（当前 responder 固定取第一个角色） | `not_started` |
| F16 | 群聊空间合理性校验：场景 + 成员时空校验，不合理可强行开启 | 未实现（`is_non_canon` 字段与 prompt 支持已就绪） | `not_started` |
| F17 | 语音输入：按住说话 → VAD → 流式 ASR | 未实现 | `blocked` |
| F18 | 语音输出：分句流式 TTS，首句延迟 ≤ 1.5 秒，可打断 | 未实现 | `blocked` |
| F19 | 前端舞台页：角色立绘选择、1v1 与群聊入口 | 缩水版试验台已跑通（`pytest tests/test_lab_page.py` + 浏览器实测）；正式舞台页未做 | `active` |
| F20 | 关系网编辑器：图形化改关系，原著值并排对照 | 未实现 | `not_started` |
| F21 | 角色一致性评测集：每角色 30 条探针题，防 OOC | `make eval`（**当前 3 角色 / 13 条**，目标每角色 30 条） | `active` |
| F22 | 会话摘要：按**锚点分段**压缩，为「≤ 当前锚点」的记忆做背景，替代只取最近 24 条原文 | 未实现 | `not_started` |
| F23 | prompt 管理：四维度（用户 × 角色 × 锚点 × 会话）覆盖 + 来源标注 + 快照留痕 | 未实现（**形态待定稿**，见 DECISIONS 2026-09-16） | `not_started` |
| F24 | 开发用 admin 用户：预置多个人设与会话，试验台可切换身份 | `make admin` + `pytest tests/test_lab_page.py`（浏览器实测见会话日志） | `passing` |

F17/F18 标 `blocked` 的原因见下方阻塞项 B02。

---

## 进行中

暂无代码改动。2026-09-16 做了一轮设计梳理（用户提的三个问题 → 根因、决策、待办），
结论见 DECISIONS.md 当天的三条与下方会话日志；待办按「下一步」执行。

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
| I10 | 用户人设不影响初始关系：prompt 里写死「素不相识」，用户在 persona 里写「我是他女儿」也照样是陌生人 | 2026-09-15 试验台实测确认；解法见「用户与角色的关系由人设语义解析产出」 |
| ~~I11~~ | ~~会话历史不按锚点过滤~~ **2026-09-16 已修复** | `list_messages` 增加 `up_to_anchor_seq`，送进模型的历史只保留「此刻及之前」；那句自相矛盾的「中间的事你未必知晓」也改掉了。验证：`pytest tests/test_anchor_scoped_memory.py` |

---

## 下一步（按优先级）

2026-09-16 按「关系由人设解析产出 + 记忆按锚点分层」两条决策重排，顺序即依赖顺序。

1. **F12 重定义：人设语义 → 结构化关系**：保存人设时解析一次并落库（可手改、手改优先），
   一次声明产出两个方向；prompt 以结构化结果为准、persona 原文作背景。这是「不再默认素不相识」的落地。
   **验收样本已经备好**：`admin`（路人）、`admin-daughter`（玉娆，林冲的私生女）、`admin-lover`（苏娘），
   现在三者在 prompt 里都是「素不相识，初次照面」——改完这条，玉娆与苏娘那两行必须变。
2. **F14 关系演化（锚点版）**：互动后按阈值推进 `stage`，往 `relationship_changes` 落记录
   （该表要加 `anchor_id`，现在只能靠 `message_id` 反查）。
3. **F22 摘要按锚点分段** + `memories.learned_at_anchor_id`：每章一条该章对话摘要，
   prompt 取「≤ 当前锚点」的那些。这两处要改表（现在没有锚点列）。
4. **F23 prompt 按锚点维护 + 四维度覆盖**：存储形态已定稿（锚点输入 + 卡片/模板版本 + 真实快照），
   接着做覆盖表、来源标注与留痕。
5. **persona 其余字段进 prompt**：background / appearance / speech_style / free_note 现在存了没用。
6. **F21 扩面**（探针补到 12 角色）、**I08 市井线补状态行**：不阻塞主线，可穿插做。
7. **拿试验台找人试**（不是开发任务，但别忘）：让一两个没读过《水浒传》的人在第十回和第七十一回
   各问林冲同一句话，看「拨时间线，人变了」能不能让人「哦」一声。不通过就该改产品方向。

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

### 2026-09-15 · F21 角色一致性评测（缩水版跑通）

- 产出：`evals/character_probes.json`（探针集）、`scripts/eval_characters.py`（跑批与判定）、
  `evals/canon_facts.py`（未来事实章回表，与 F13 的分层测试共用一份，避免两边漂移）、
  `make eval`。
- 判据分三层：① 送给模型的 prompt 必须接上时间线（确定性）；② 回复里不许出现出戏词、现代词、
  别人的自称、提前知道未来；③ `expect_any` 只用在「直接问、必须正面回答」的探针上。
- 第一版 9/13：四条「不合规」全是探针写得太字面——要求武松向陌生人承认杀了张都监一家、
  要求高俅向陌生人交代林冲去向、把「那畜生」当成没提老虎。模型答得很像本人，是判据错了。
- 第二版 11/13：① 「什么第六把交椅，没听人讲过」这种**复述用户的话来反驳**被误判成泄漏；
  ② 我要求 prompt 里出现「不共戴天的死仇」，但那是群聊里高俅在场时才注入的关系边，1v1 本该没有。
- 修法：不引入「否认词窗口」这种启发式（自检时发现「不错，日后招安」会被当成否认，假阴性），
  改成一条硬规矩——**探针里不写被禁的词**，于是回复里一旦出现就一定是泄漏。
- 终版 13/13 通过；并做了反面自检：喂一条「我是AI助手…按原著剧情…洒家」的假回复，
  八类问题全部抓到（超长 / 出戏词 / 别人的自称 / 提前知道未来）。
- 采样（第十回 vs 第七十一回的林冲，同一句话的两种答法）：
  第十回问「你日后要坐第六把交椅吗」→「林某如今只是个脸上刺字的配军，正往梁山去投王伦，哪来的什么大头领」；
  第七十一回问「你坐第几把交椅」→「林某坐第六把交椅，天雄星便是」。

### 2026-09-15 · F19 缩水版：试验台页面（终于能用手拨时间线了）

- 产出：`app/static/lab.html`（单文件，无构建工具、无框架）+ `GET /` 托管路由 + `tests/test_lab_page.py`（2 项）。
- 页面内容：章回滑块（12 个锚点）、改「我的人设」、12 张角色卡（带「此刻在场 / 尚未登场 / 已不在人世」徽章）、
  1v1 对话（真 SSE 流式，逐字出）、底部可展开**此刻发给模型的原样 prompt**。
- 浏览器实测（驱动真实点击，不是读代码猜）：标题正确；provider 徽章 `deepseek / deepseek-v4-flash`；
  第二回时 12 张卡里只有史进、高俅是「此刻在场」；滑块拨到第十回后，林冲卡变成「此刻在场 / 刺配的教头」，
  聊天头部显示「刺配的教头 · 沧州往梁山途中」；发一句「教头，这雪夜往哪里去？」，真实模型回
  「林某往哪里去，与足下何干？这大雪天，你独自一个在这荒郊，倒来问我。」；
  prompt 面板 876 字，含「刺配」、不含「交椅」——F13 在界面上也验到了；
  改人设保存后，prompt 里「对 张三（…与林教头有旧）」立刻跟着变。
- 顺手修掉的真问题：布局写死 `calc(100vh - 53px)`，而 header 实际高 56px，于是 header 被切掉 3px、
  展开 prompt 面板时整页溢出（聊天区不收缩）。改成 flex + `min-height: 0`，实测 headerTop=0、
  `scrollHeight == innerHeight`（720），不再溢出。这个 bug 光看代码看不出来，是量了几何才知道。
- 有意不做：不引框架、不做路由、不碰立绘与群聊入口——那是正式舞台页（F19 全部）的事，
  而它卡在 B03（平台优先级）。试验台能扔，先让人用手感受产品。
- `make check`：28/28 测试（新增 2 项）+ 数据冒烟 7/7 + 密钥检查通过。

### 2026-09-16 · 设计梳理：关系不该默认「素不相识」、记忆要按锚点分层

用户在试验台上试出三个问题（原文见当天对话），这一轮**只做分析不改代码**，结论如下。

- **问题 1：用户设定的人设不影响初始关系。** 实测：`lab-ui` 与林冲聊了 10 轮，
  `user_character_relations` 仍是 `stage=stranger / affinity=0 / trust=0`（只有 `interaction_count=10` 在涨）。
  三处断点：① `user_personas` 六个字段里 prompt 只用了 `name` 和 `identity`，而 `identity` 只当括号注解；
  ② 全仓库**没有任何代码写** `stage / char_affinity / char_trust / char_wariness`；
  ③ 没有写关系的接口。另有结构性问题：用户边（`relationship_edges.from_kind='user'`）在 schema 里早就支持，
  但 `v_effective_relationships` 明确排除了它，改用另一张表 `user_character_relations`——**同一概念两套机制并存，两套都没接通**。
  决策：关系由人设语义解析产出（见 DECISIONS 2026-09-16 第一条）。记入 I10。
- **问题 2：记忆没有锚点维度。** `messages.anchor_id` **本来就记了**（实测该会话每条消息都标着第十回），
  但 `app/services/chat.py:133` 的 `list_messages(session_id, limit=24)` 只按会话取最近 24 条，不看锚点。
  于是往前滑必然记得、往回滑照样记得未来。更自相矛盾的是：prompt 一边写「中间的事你未必知晓」，
  一边把那些话原文塞进同一个 prompt。决策：记忆按锚点分层（往前携带、往回忘掉未来），记入 I11。
- **问题 3：prompt 是实时装配、不留痕、没有管理面。** 现在只有 `/prompt-preview` 看「下一句」，
  发完即忘；四维度（用户 × 角色 × 锚点 × 会话）覆盖没有地方写。新增 F23（prompt 管理）与
  F24（开发用 admin 用户），存储形态待定稿。
- **与既有设计的冲突（已显式处理）**：PLAN §3.1「记忆随身携带」与 §6「好感度跨时间线延续」与
  「滑回过去要忘掉未来」相抵触。按用户 2026-09-16 的明确表态（过去不会忘、未来要忘掉），
  在 DECISIONS 里**部分覆盖**了这两条：向前携带不变，新增「不携带未来」。
- 这一轮没动任何代码、表或接口；改动仅限本文件与 DECISIONS.md。

### 2026-09-16 · I11 修复：记忆按锚点分层（往前携带、往回忘掉未来）

- `repo.list_messages` 增加 `up_to_anchor_seq`：`LEFT JOIN timeline_anchors` 后只取 `a.seq <= 当前`
  （`anchor_id IS NULL` 的系统消息始终可见）；`prepare_turn` 两处调用都传当前锚点。
- `build_system_prompt` 里那句「中间的事你未必知晓」删了——历史现在真的只到当前锚点，
  所以改成说「隔了些时日」，而不是说「你不记得」（旧写法与塞进上下文的原文自相矛盾）。
- 有意保留：`GET /api/sessions/{id}/messages` 仍返回整条日志。用户自己记得全部，
  被过滤的只是**角色能看到的那部分**；界面上看到的日志与角色记得的东西本来就不是一回事。
- 验证：`pytest tests/test_anchor_scoped_memory.py`（3 项：往前携带 / 往回忘掉 / 日志原样）。
  变异验证：把锚点过滤关掉，用例立刻报「未来聊过的内容漏进了上下文」，撤回即绿。
- `make check`：31/31 测试 + 数据冒烟 7/7 + 密钥检查通过。

### 2026-09-16 · F24 完成：开发用身份 + 试验台可切换（顺手给 F12 备好验收样本）

- `scripts/bootstrap_admin.py` + `make admin`：建三个固定身份（幂等，重复跑只复用不重建）——
  `admin`（张三，酒铺掌柜）、`admin-daughter`（玉娆，林冲失散多年的私生女）、
  `admin-lover`（苏娘，林冲在东京时的旧相识），每人预置一个与林冲的一对一会话（第十回）。
  它们同时就是 F12 的验收样本：前一个该解析成「无关系」，后两个该解析出关系。
- 两个开发接口：`GET /api/users/{id}/persona`（以前只有 PUT，切换身份后读不回人设）、
  `GET /api/dev/users`（给切换器列账号；默认前缀白名单 `admin,lab-ui,demo`，传 `prefixes=all` 看全部）。
  加白名单是因为开发库里躺着十几个测试跑出来的 `iso-*` / `test-*` 账号，不滤会淹掉下拉框。
- 试验台新增「以谁的身份进入」下拉框：切换身份 = 换一整套上下文（人设、时间线、角色可用性、
  会话与 prompt 面板全部重置），并用 `localStorage` 记住上次选的。
- 浏览器实测：下拉框 5 项（admin-lover / admin-daughter / admin / lab-ui / demo）；
  默认进 `admin`（张三），人设从服务端读回；切到 `admin-daughter` 后
  人设表单变成「玉娆 / 林冲失散多年的私生女，随母姓」，选中的角色被清空；
  再点林冲，第十回的 prompt 里那一行是
  「对 玉娆（林冲失散多年的私生女，随母姓）：**素不相识，初次照面**」——
  I10 的现场，也是 F12 改完必须翻掉的那一行。
- 验证：`make admin` 跑通；`pytest tests/test_lab_page.py`（5 项，含身份切换器与两个开发接口）；
  浏览器实测见上。`make check`：34/34 测试 + 数据冒烟 7/7 + 密钥检查通过。
