# 标准化的操作命令。直接跑 `make` 或 `make help` 看全部。
SHELL    := /bin/bash
VENV     := .venv
PY       := $(VENV)/bin/python
UVICORN  := $(VENV)/bin/uvicorn
PSQL     ?= psql

# 数据库连接串。默认对应 Docker 容器 mypg 映射出来的 5433 端口。
DB_URL   ?= postgresql://postgres:123456@127.0.0.1:5433/bistro
BISTRO_DATABASE_URL ?= $(DB_URL)
export BISTRO_DATABASE_URL

.PHONY: help setup db db-reset demo run test check status

help: ## 列出所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-9s\033[0m %s\n", $$1, $$2}'

setup: ## 建虚拟环境并安装依赖（已做过可跳过）
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -e ".[dev]"

db: ## 建库 / 建表 / 灌基础数据（已存在则跳过建库）
	./scripts/dev_db.sh

db-reset: ## 删库重建，会清空数据
	./scripts/dev_db.sh --drop

demo: ## 造演示数据：张三 × 林冲 × 第十回
	$(PY) scripts/bootstrap_demo.py

run: ## 起服务 http://127.0.0.1:8000
	$(UVICORN) app.main:app --reload

test: ## 跑测试（需要数据库在跑）
	BISTRO_TEST_DATABASE_URL=$(DB_URL) $(PY) -m pytest -q

check: test ## 一致状态验证：测试 + 数据冒烟测试
	$(PSQL) "$(DB_URL)" -q -v ON_ERROR_STOP=1 -f db/tests/verify_seed.sql

status: ## 打印 PROGRESS.md 的快照段
	@sed -n '/^## 快照/,/^---$$/p' PROGRESS.md
