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
make run        # 起服务 http://127.0.0.1:8000
make check      # 一致状态验证（测试 + 数据冒烟测试）
```

默认使用离线 `mock` provider，不需要任何 API Key 就能把整条链路跑通。
接真实模型时复制 `.env.example` 为 `.env`，把 `BISTRO_LLM_PROVIDER` 改成
`openai_compat` 并填上 `BISTRO_LLM_BASE_URL` / `BISTRO_LLM_API_KEY`。

> **Python 版本说明**：本机 homebrew 的 python@3.11 / 3.12 因为 `libexpat`
> 链接失效无法创建虚拟环境（`Symbol not found: _XML_SetAllocTrackerActivationThreshold`），
> 当前用的是系统自带的 3.9。修复方式：`brew reinstall python@3.12`。

## 后端接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查，回显当前 provider |
| GET | `/api/works/{slug}/anchors` | 时间锚点列表（章回滑块的数据源） |
| GET | `/api/works/{slug}/characters?user_id=` | 角色列表；带 user_id 时附带该时间点下的状态与可选性 |
| POST | `/api/users` | 建用户 |
| PUT | `/api/users/{id}/persona` | 设置「我在这个故事里是谁」 |
| GET/PUT | `/api/users/{id}/timeline` | 读取 / 拨动时间线（按 `chapter_no` 或 `anchor_seq`） |
| POST | `/api/sessions` | 建会话，`direct` 或 `group` |
| GET | `/api/sessions?user_id=` | 会话列表 |
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
