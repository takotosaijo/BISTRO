# 水浒 · 角色对话与时间线系统

一个「和小说角色聊天」的产品：用户可以选角色一对一对话，也可以多选群聊；可以设定自己与角色的关系，也可以改写角色与角色之间的原著关系；文本与语音双向支持；每个会话互相隔离。

与「复现剧情」不同，本产品的核心是**世界状态切片**：用户把时间线拨到某一回，系统据此决定「此刻谁认识谁、谁在做什么、谁只知道哪些事」，而对话本身永远发生在当下。

> **新会话请先读 [AGENTS.md](AGENTS.md)**：那里是项目入口（怎么跑、怎么验证、硬约束、
> 上下班流程）。当前进度看 [PROGRESS.md](PROGRESS.md)，历史决策看 [DECISIONS.md](DECISIONS.md)。

## 已确认的产品决策

| 项 | 决策 |
|---|---|
| 作品 | 《水浒传》 |
| 版本 | 100 回本 |
| 版权 | 公有领域，无授权风险 |
| 首发角色 | 12 人（梁山线 8 + 市井/对立线 4） |
| 时间线粒度 | UI 按章回呈现，数据按 12 个事件锚点实现 |
| 首期剧情模式 | 正史模式（用户互动只改关系与记忆，不改原著大事） |

## 仓库结构

| 路径 | 内容 |
|---|---|
| [AGENTS.md](AGENTS.md) | 项目入口：怎么跑、怎么验证、硬约束、会话上下班流程 |
| [PROGRESS.md](PROGRESS.md) | 状态持久化：快照、功能清单（含验证命令）、已知问题、下一步、会话日志 |
| [DECISIONS.md](DECISIONS.md) | 决策日志：为什么这么选、否决了什么 |
| [Makefile](Makefile) | 标准命令：`make setup / db / demo / run / test / check` |
| [docs/PLAN.md](docs/PLAN.md) | 完整开发方案：产品形态、时间线模型、关系覆盖规则、群聊调度、语音链路、分期计划 |
| [db/schema.sql](db/schema.sql) | 数据模型 DDL（PostgreSQL 15 + pgvector） |
| [db/seed.sql](db/seed.sql) | 基础数据：12 个角色卡、12 个时间锚点、时序关系边、角色状态快照 |
| [db/CONSTRAINTS.md](db/CONSTRAINTS.md) | 数据库模块的硬约束（改表前必读） |
| [db/tests/verify_seed.sql](db/tests/verify_seed.sql) | 冒烟测试：时间线关系解析、用户覆盖优先级、状态填充 |
| [app/](app/) | FastAPI 后端：对话管线、prompt 装配、模型层抽象 |
| [scripts/](scripts/) | 建库脚本与演示数据初始化 |
| [tests/](tests/) | 第 0 期验收测试（时间线 → 状态 → 关系 → prompt → 生成 → 落库） |

## 快速开始

```bash
make setup      # 建虚拟环境 + 装依赖
make db-reset   # 建库、建表、灌基础数据、跑冒烟测试
make demo       # 造演示数据：张三 × 林冲 × 第十回
make run        # 起服务 http://127.0.0.1:8002
make check      # 一致状态验证（测试 + 数据冒烟测试）
```

默认使用离线 `mock` provider，不需要任何 API Key 就能把整条链路跑通。

### 试验台页面

```bash
make admin    # 造几个开发身份：路人 / 女儿 / 旧相识（幂等）
make run      # 然后打开 http://127.0.0.1:8002/
```

单文件 HTML（[app/static/lab.html](app/static/lab.html)，无构建工具、无框架），由服务直接托管。
左边从上到下是「我的角色」（`make admin` 造的那几个身份，选中即切换身份）、
「我在这个故事里是谁」（编辑当前身份，改完名字上面对应项立刻跟着变）、章回滑块、
12 张角色卡（带「此刻在场 / 尚未登场 / 已不在人世」徽章）；中间是 1v1 对话，
底部随时展开看**此刻发给模型的原样 prompt**。拨动滑块再问同一句话，就能看出「同一个人换了处境」；
换个身份再问，就能看出「不同的人设会看到什么」。

一个账号可以有多个身份（`personas`）：关系、会话、记忆挂在身份上，时间线挂在账号上。
换身份等于换了一个人，所以切过去时对话与 prompt 面板会清空——历史不该被两个身份共用。

保存身份时会解析自述里与角色的关系（真实模型读自然语言，离线时走关键词规则），
落成**双向**的关系声明：用户怎么看他、他怎么看她，两边可以完全不同——
写「林冲失散多年的私生女」时，她认定他是爹，而他不认得她。prompt 的「你与在场之人」以它为准。

- 解析只在**保存身份时**发生。F12 上线之前存下的身份没有声明，跑 `make reparse` 补一次
  （默认只处理 admin/demo，`ARGS=--all` 处理全部——那会真的花额度）。
- 用户手改过的声明（`relationship_edges.is_manual`）不会被重新解析覆盖。
- 试验台的「我的角色」下面会显示当前身份**已声明**了哪些关系；没有的话，多半是自述里
  没点名提到角色（「东京城里开酒铺的掌柜」解析不出关系，「林冲失散多年的私生女」可以）。

它不是产品前端（F19 的正式形态还没定平台），是给人用手感受用的试验台——所以随时可以扔。

### 接真实模型（当前用 DeepSeek）

配置只写在 `.env` 里（`.env` 已被 `.gitignore` 忽略，不进版本库）：

```ini
BISTRO_LLM_PROVIDER=deepseek
BISTRO_LLM_BASE_URL=https://api.deepseek.com/v1   # 留空则用 DeepSeek 官方默认地址
BISTRO_LLM_API_KEY=                               # 在这里填密钥，别填进 .env.example
BISTRO_LLM_MODEL=deepseek-v4-flash
BISTRO_LLM_THINKING=auto                          # auto | disabled，见下
```

`deepseek` 与 `openai_compat` 都是 OpenAI 兼容协议，区别只是默认地址；
换供应商就是改 `BISTRO_LLM_PROVIDER` 一行。provider 名写错会直接报错，
不会静默退回 mock——否则「以为接了真实模型、其实在跟 mock 说话」很难发现。

验证真实模型是否接通：

```bash
.venv/bin/python scripts/check_llm.py                  # 进程内直连：验证整条链路出正文
make run                                               # 另开一个终端起服务
.venv/bin/python scripts/check_llm.py --http http://127.0.0.1:8002   # 首字延迟才准
```

`deepseek-v4-flash` 是推理模型，默认会先流式输出一大段思考
（`reasoning_content`），正文要等思考结束才出现，首字延迟明显变长。
`BISTRO_LLM_THINKING=disabled` 可以关掉思考换更快的响应，实测同一句话
首字延迟从 2.5s 降到 1.8s（短句；长回复差距更大）。默认保持 `auto`。

### 角色一致性评测（改了 prompt 就跑）

```bash
make eval                                                    # 全部探针（真实模型，消耗额度）
.venv/bin/python scripts/eval_characters.py --character lin-chong
```

探针集在 [evals/character_probes.json](evals/character_probes.json)，判定逻辑在
[scripts/eval_characters.py](scripts/eval_characters.py)：先查送给模型的 prompt 有没有接上
时间线（确定性判定），再查回复有没有出戏、提前知道未来、口吻串到别人身上。
它**不判**「像不像本人」——那是人看的。

### 密钥纪律

- 密钥只写在 `.env`，`.env.example` 里永远留空，README / 日志 / 提交信息里不出现密钥。
- `make check` 会顺带跑 `make secrets`：确认 `.env` 仍被忽略、被追踪的文件与提交历史里都没有密钥形状的字符串。
- 万一密钥进过提交：先撤销这把 key，再改写历史（`git filter-repo` / BFG），仅删文件是不够的。

> **Python 版本说明**：本机 homebrew 的 python@3.11 / 3.12 因为 `libexpat`
> 链接失效无法创建虚拟环境（`Symbol not found: _XML_SetAllocTrackerActivationThreshold`），
> 当前用的是系统自带的 3.9。修复方式：`brew reinstall python@3.12`。

## 后端接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查，回显当前 provider |
| GET | `/api/works/{slug}/anchors` | 时间锚点列表（章回滑块的数据源） |
| GET | `/api/works/{slug}/characters?persona_id=` | 角色列表；带 persona_id 时附带该时间点下的状态与可选性 |
| POST | `/api/users` | 建账号 |
| POST | `/api/users/{id}/personas` | 在账号下建一个身份（「我在这个故事里是谁」） |
| GET | `/api/users/{id}/personas` | 这个账号的全部身份（试验台「我的角色」列表） |
| GET/PUT | `/api/personas/{id}` | 读 / 改一个身份 |
| GET/PUT | `/api/users/{id}/timeline` | 读取 / 拨动时间线（按 `chapter_no` 或 `anchor_seq`） |
| POST | `/api/sessions` | 以某个身份建会话（`persona_id`），`direct` 或 `group` |
| GET | `/api/sessions?persona_id=` | 这个身份的会话列表 |
| GET | `/api/sessions/{id}/messages` | 会话历史 |
| POST | `/api/sessions/{id}/messages` | 发消息，返回完整回复 |
| POST | `/api/sessions/{id}/messages/stream` | 发消息，SSE 流式返回 |
| GET | `/api/sessions/{id}/prompt-preview` | **把将要发给模型的 system prompt 原样吐回来** |

`prompt-preview` 是调角色最有用的一个接口：改人设、改关系、拨时间线之后，
先看它，再看回复。不看 prompt 就改模型行为，等于闭着眼睛调。

## 数据库

开发库用 [scripts/dev_db.sh](scripts/dev_db.sh) 创建，它会自动检测 pgvector：
装了就用 `vector(1024)` 列与 hnsw 索引，没装就把向量列降级成 jsonb。

当前实际使用的是 Docker 容器 `mypg`（`pgvector/pgvector:pg16`，已装 vector 0.8.6），
连接串写在 `.env` 的 `BISTRO_DATABASE_URL`，即 `127.0.0.1:5433`。

> **为什么容器映射到 5433**：homebrew 的 PostgreSQL 15 占用了 `127.0.0.1:5432`，
Docker 的端口转发只能绑到通配地址，回环访问 `127.0.0.1:5432` 会连到 homebrew 那个库
（超级用户 `xiaobing`）而不是容器。所以容器改映射到 5433，两边互不干扰。

容器的启动命令（数据在卷 `37d4086188b0…` 里，重建时必须显式挂回）：

```bash
docker run -d --name mypg \
  -e POSTGRES_PASSWORD=123456 \
  -p 5433:5432 \
  -v 37d4086188b086c57a3bd067e3c3074907099f3c4e20bd39f06da7a8351d8e6c:/var/lib/postgresql/data \
  pgvector/pgvector:pg16
```

## 测试

```bash
BISTRO_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/bistro .venv/bin/python -m pytest -q
```

需要 PostgreSQL 15+。装了 [pgvector](https://github.com/pgvector/pgvector) 可启用向量检索；
没装时开发脚本会把向量列降级为 jsonb。

## 设计上最容易做错的三处

1. **关系边是有向的**。A→B 与 B→A 是两条独立记录，改一边不会同步另一边。
2. **角色状态是填充语义**。只需在状态真正变化时写一行，`v_character_states_resolved` 会把最近一次状态沿时间线铺开。
3. **用户覆盖优先于原著，但同样受时间线约束**。三条边同时存在时，由 `v_effective_relationships` 裁定，不要在应用层再写一遍。
