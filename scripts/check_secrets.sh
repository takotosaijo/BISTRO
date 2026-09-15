#!/usr/bin/env bash
# 密钥泄漏检查：确认 API Key 没进「会被提交/推送」的地方。
#
# 查三件事，任何一件不通过就返回非 0：
#   1. .env 仍然被 .gitignore 忽略（密钥的唯一合法住处）
#   2. 被 git 追踪的文件里没有厂商密钥形状的字符串
#   3. 提交历史里没有出现过密钥（历史里的密钥照样会被 push 出去）
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# sk- 开头的厂商密钥（DeepSeek / OpenAI 等）；16 位起，避开文档里的示意短串
PATTERN='sk-[A-Za-z0-9_-]{16,}'
STATUS=0

if [[ -f .env ]]; then
  if git check-ignore -q .env; then
    echo "OK    .env 已被 .gitignore 忽略，密钥不会被提交"
  else
    echo "危险！.env 没有被忽略，密钥会被提交。请先修 .gitignore。"
    STATUS=1
  fi
else
  echo "跳过  .env 不存在（还没有本地密钥）"
fi

if git grep -nIE "$PATTERN" -- . >/dev/null 2>&1; then
  echo "危险！被追踪的文件里发现疑似密钥："
  git grep -nIE "$PATTERN" -- . || true
  STATUS=1
else
  echo "OK    被追踪的文件里没有疑似密钥"
fi

if git log --all -p 2>/dev/null | grep -qE "$PATTERN"; then
  echo "危险！提交历史里出现过疑似密钥，push 之后会一起暴露。"
  echo "      处理方式：撤销该 key 并改写历史（git filter-repo / BFG）。"
  STATUS=1
else
  echo "OK    提交历史里没有疑似密钥"
fi

exit "$STATUS"
