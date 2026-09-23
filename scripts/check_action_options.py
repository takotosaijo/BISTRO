"""F26 真实模型验针：跨轮跑一遍，看选项**只在角色要东西时才弹**，而且主语是用户。

    make eval-options
    .venv/bin/python scripts/check_action_options.py --http http://127.0.0.1:8002

为什么必须是跨轮的：2026-09-23 报的两个 bug（选项每轮都弹、选项写成角色口吻）
在**单轮**里全都看不出来——离线测试用的是简单规则版判定器，手测那一轮又恰好正常。
只有连着跑几轮、并且真的点一次选项，才能看见「点完还弹」和「弹出角色的动作」。

探针跑 6 轮：认亲 → 点他给的选项 → 寒暄 → 再点一次 → 提旧疤 → 要一句认亲的话。
判据：
  ① 至少有一轮给了选项（功能还活着）
  ② 6 轮里有选项的**不超过 3 轮**（原来 27 个弹过的会话里 24 个是每轮都弹）
  ③ 没有任何一轮被硬闸拦下（被拦说明模型还是写空了回复、或写了角色口吻的选项）
退出码：0 通过 / 1 不合规 / 2 调用出错。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.providers import build_provider  # noqa: E402

WORK = settings.default_work_slug
CHAPTER = 10
MAX_TURNS_WITH_OPTIONS = 3

# (type, text)：type=text 是自己打字；type=option 表示「点上一轮的第一个选项」
SCRIPT: List[Tuple[str, str]] = [
    ("text", "爹，女儿玉娆给你磕头。我娘是苏氏，东京人氏——当年与你未及提亲，你就出了事。"
             "娘临终前让我来寻你，说无论如何要找到你。"),
    ("option", ""),
    ("text", "爹，你这些年可好？"),
    ("option", ""),
    ("text", "娘还说：你左肩胛上有一道旧疤，是那年替她挡刀留下的。"),
    ("text", "女儿不求别的，只求你说一句认我的话。"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="F26 情境选项真实模型验针（跨轮）")
    parser.add_argument("--character", default="lin-chong", help="角色 slug，默认 lin-chong")
    parser.add_argument("--http", metavar="BASE_URL", default=None, help="打已启动的服务")
    parser.add_argument("--timeout", type=float, default=180.0, help="单次调用超时（秒）")
    return parser.parse_args()


async def setup(client: httpx.AsyncClient, character_slug: str) -> Dict[str, Any]:
    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"options-{uuid.uuid4().hex[:8]}", "display_name": "玉娆"},
        )
    ).json()
    persona = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={
                "work_slug": WORK,
                "name": "玉娆",
                "identity": "林冲失散多年的私生女，随母姓",
                "background": "娘亲苏氏临终前交给我半块刻着「苏」字的玉佩，说另外半块爹带走了。",
            },
        )
    ).json()
    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": CHAPTER}
    )
    session = (
        await client.post(
            "/api/sessions",
            json={
                "persona_id": persona["id"],
                "work_slug": WORK,
                "session_type": "direct",
                "character_slugs": [character_slug],
            },
        )
    ).json()
    return {"user": user, "persona": persona, "session": session}


async def run(args: argparse.Namespace) -> int:
    provider = build_provider(settings)
    print(f"provider : {provider.name} / {provider.model}")
    print(f"剧本     : {len(SCRIPT)} 轮（认亲 → 点选项 → 寒暄 → 点选项 → 旧疤 → 要一句话）")
    print("-" * 72)

    if provider.name == "mock":
        print("当前是离线 mock provider：这条探针要验的是真实模型的判定，先退出。")
        return 0

    transport = None if args.http else httpx.ASGITransport(app=app)
    await db.connect()
    problems: List[str] = []
    with_options = 0
    drops: List[str] = []
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url=args.http or "http://options", timeout=args.timeout
        ) as client:
            ctx = await setup(client, args.character)
            session_id = ctx["session"]["id"]
            print(f"会话     : session={session_id}")
            print("-" * 72)

            last_options: List[Dict[str, str]] = []
            for index, (kind, text) in enumerate(SCRIPT, start=1):
                message_kind = "text"
                if kind == "option":
                    if last_options:
                        text = last_options[0]["action"]
                        message_kind = "narration"
                        print(f"[{index}/{len(SCRIPT)}] （点第一个选项）{text}")
                    else:
                        text = "爹，女儿听你的。"
                        print(f"[{index}/{len(SCRIPT)}] （上一轮没有选项，改成打字）{text}")
                else:
                    print(f"[{index}/{len(SCRIPT)}] 用户：{text}")

                response = await client.post(
                    f"/api/sessions/{session_id}/messages",
                    json={"content": text, "kind": message_kind},
                )
                response.raise_for_status()
                body = response.json()
                reply = body["reply"]["content"]
                options = body.get("action_options") or []

                print(f"         林冲：{reply}")
                if options:
                    with_options += 1
                    print(f"         选项 {len(options)} 条：")
                    for option in options:
                        print(f"           · {option['label']}｜{option['action'][:46]}")
                else:
                    print("         （没有选项）")

                stored = await _stored_drop(session_id, body["reply"]["id"])
                if stored:
                    drops.append(f"第{index}轮：{stored}")
                    print(f"         ⚠ 被硬闸拦下：{stored}")
                last_options = options

            print("-" * 72)
            if with_options == 0:
                problems.append("一轮都没给过选项——该给的时候也没给，功能等于没接上")
            if with_options > MAX_TURNS_WITH_OPTIONS:
                problems.append(
                    f"{len(SCRIPT)} 轮里有 {with_options} 轮弹了选项（上限 {MAX_TURNS_WITH_OPTIONS}）"
                    "——又回到「点完还弹」了"
                )
            for drop in drops:
                problems.append(f"被硬闸拦下（模型写错了）：{drop}")
            print(f"有选项的轮数：{with_options}/{len(SCRIPT)}（上限 {MAX_TURNS_WITH_OPTIONS}）")
            print(f"留库备查：session={session_id}")
    finally:
        await db.disconnect()

    if problems:
        print("结果     : 不合格")
        for problem in problems:
            print(f"  ✗ {problem}")
        return 1
    print("结果     : 通过——只有他要东西的那几轮弹了选项，且没有一轮写成角色口吻")
    return 0


async def _stored_drop(session_id: int, message_id: int) -> Optional[str]:
    """看这条回复有没有被硬闸拦下（拦下会写 action_options_dropped）。"""

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT meta->>'action_options_dropped' AS dropped FROM messages WHERE id = $1",
            message_id,
        )
    return row["dropped"] if row else None


def main() -> int:
    try:
        return asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - 验针脚本，打印原因比抛栈有用
        print(f"\n验针中断：{type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
