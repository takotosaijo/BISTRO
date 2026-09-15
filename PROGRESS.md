# PROGRESS.md — 项目状态

状态持久化文件。新会话读完这一份（加上 [AGENTS.md](AGENTS.md)）就应该能接上工作。

**规则**：功能项的状态只能由验证命令的结果驱动。没跑过 `make check`，不许标 `passing`。

新会话接上工作只需要三步：`make check` → 读下面「快照」与「进行中」→ 从「下一步」挑一件。

---

## 快照

- 更新时间：2026-09-15
- 最新提交：本轮三个提交——工程管理骨架 → 接入 DeepSeek → 开发服务端口改 8002；
  工作区干净（提交号看 `git log --oneline -3`）
- 测试：**16/16 通过**（`make test`）
- 数据冒烟测试：**7/7 通过**（`db/tests/verify_seed.sql`）
- 密钥泄漏检查：**通过**（`make secrets`：`.env` 仍被忽略、被追踪文件与提交历史里都没有密钥）
- 数据库：Docker 容器 `mypg`（`pgvector/pgvector:pg16`，vector 0.8.6），`127.0.0.1:5433/bistro`
- 数据量：12 角色 / 12 时间锚点 / 45 状态行 / 23 关系边 / 5 事件 / 19 表 / 3 视图
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
| F10 | 会话隔离：两个会话的消息、上下文、记忆互不串 | **缺验证**：需补"两个会话并行不串"的测试 | `active` |
| F11 | 记忆层：写入抽取 + 向量检索 + 召回进 prompt | 未实现 | `not_started` |
| F12 | 用户↔角色的显式关系声明（`from_kind='user'`），含 `from_here` 等生效语义 | 未实现 | `not_started` |
| F13 | 角色卡分层：恒定层与时间线层拆开，修掉未来信息泄露 | 未实现（现仅靠 prompt 兜底提示缓解） | `not_started` |
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

暂无。当前工作区只有文档改动，没有半成品代码。

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
| I01 | 角色卡 `identity` 是按全书视角写的（如林冲那条含"坐第六把交椅"），早期锚点会泄露未来 | 已在 prompt 加兜底提示（受 `spoiler_guard` 控制，有测试断言）；根治方案见 F13 |
| I02 | 群聊的 responder 固定取第一个角色 | 第 0 期的临时实现，第 2 期换发言调度 |
| I03 | 好感度只增不减 | `user_character_relations` 无衰减规则，长期会失真 |
| I04 | 用户覆盖目前只覆盖角色↔角色 | 用户自己的关系声明还没接进 prompt |
| I05 | 没装 pgvector 的环境会把向量列降级为 jsonb | 仅影响向量检索，本机容器已具备 pgvector |
| I06 | 开发环境是 Python 3.9 | homebrew 的 3.11/3.12 因 libexpat 链接失效不可用；修复命令 `brew reinstall python@3.12` |
| I07 | 数据库容器重启策略是 `no` | Mac 重启后容器不会自动拉起，`make check` 会连不上库 |

---

## 下一步（按优先级）

1. **补 F10 的验证**：加一个"两个会话并行互不串消息与上下文"的测试，把会话隔离从 `active` 推到 `passing`。
2. **F11 记忆层**：pgvector 已就绪，写"对话后抽取记忆 → 向量召回 → 进 prompt"的闭环，并补验证命令。
3. **F12 用户↔角色显式关系**：把 `relationship_edges` 的 `from_kind='user'` 接进 prompt，打通"我对林冲是结义兄弟"。
4. **F13 角色卡分层**：拆恒定层与时间线层，彻底修掉 I01。
5. **F21 角色一致性评测集**：真实模型已接通（DeepSeek `deepseek-v4-flash`），但「像不像本人」
   仍无量化结论；先用每角色 30 条探针题把这条风险关掉。跑一轮真实模型的命令见 README。

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
