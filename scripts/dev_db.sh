#!/usr/bin/env bash
# 开发用建库脚本：建库 → 建表 → 灌基础数据。
#
#   scripts/dev_db.sh            增量执行（已存在则跳过建库）
#   scripts/dev_db.sh --drop     先删库重建
#
# pgvector 是可选的：装了就按 db/schema.sql 原样建表；
# 没装就把向量列临时降级为 jsonb（仅本地开发，不影响线上结构）。
set -euo pipefail

DB_NAME="${BISTRO_DB_NAME:-bistro}"
PSQL="${PSQL:-psql}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${1:-}" == "--drop" ]]; then
  echo "==> 删除数据库 $DB_NAME"
  "$PSQL" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;"
fi

if ! "$PSQL" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  echo "==> 创建数据库 $DB_NAME"
  "$PSQL" -d postgres -c "CREATE DATABASE $DB_NAME;"
fi

HAS_VECTOR="$("$PSQL" -d "$DB_NAME" -tAc "SELECT 1 FROM pg_available_extensions WHERE name='vector'" || true)"
SCHEMA_FILE="$ROOT_DIR/db/schema.sql"

if [[ "$HAS_VECTOR" == "1" ]]; then
  echo "==> 检测到 pgvector，按原结构建表"
else
  echo "==> 未安装 pgvector，向量列降级为 jsonb（仅开发环境）"
  echo "    需要完整功能请执行：brew install pgvector"
  SCHEMA_FILE="$(mktemp -t bistro_schema_novector)"
  sed -e '/CREATE EXTENSION IF NOT EXISTS vector;/d' \
      -e 's/vector(1024)/jsonb/g' \
      -e 's/USING hnsw (embedding vector_cosine_ops)/USING btree (id)/g' \
      "$ROOT_DIR/db/schema.sql" > "$SCHEMA_FILE"
fi

echo "==> 建表"
"$PSQL" -d "$DB_NAME" -q -v ON_ERROR_STOP=1 -f "$SCHEMA_FILE"

echo "==> 导入基础数据"
"$PSQL" -d "$DB_NAME" -q -v ON_ERROR_STOP=1 -f "$ROOT_DIR/db/seed.sql"

echo "==> 冒烟测试"
"$PSQL" -d "$DB_NAME" -q -v ON_ERROR_STOP=1 -f "$ROOT_DIR/db/tests/verify_seed.sql"

echo
echo "完成。数据库：$DB_NAME"
echo "下一步：.venv/bin/python scripts/bootstrap_demo.py"
