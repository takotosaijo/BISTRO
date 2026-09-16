"""F22 真实模型验针：这一章的话真被压成了一条提要，而且进了 prompt 的前情提要。

    make eval-summary
    .venv/bin/python scripts/check_summary.py --http http://127.0.0.1:8002

为什么要单独一条命令：`pytest -k anchor_summary` 验的是**机制**（每章一条、窗口外的才给、
未来的不漏），用的离线规则版摘要器；「真实模型能不能把一段对话压成一句像样的提要」
只有真跑一次才知道，而且它的质量得用人眼看一下——脚本只挑硬伤（空话、出戏词、数值化关系）。

做法：造一个会话，**直接写进 30 条现成的对话**（不花额度去让角色逐轮回复），
然后跑一次真实的摘要，再打 `/prompt-preview` 看它有没有出现在前情提要里。
全程只花 **一次** 模型调用。退出码：0 通过 / 1 硬伤 / 2 调用出错。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app import repository as repo  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.providers import build_provider  # noqa: E402
from app.services.summarize import build_summarizer, refresh_anchor_summary  # noqa: E402

WORK = settings.default_work_slug
CHAPTER = 10

# 现成的一段对话：认亲的引子 + 递玉 + 答他追问的旧案（真实模型认下那条线的前半截）。
CANNED = [
    ("user", "爹，女儿玉娆给你磕头。我娘是苏氏，东京人氏——当年与你未及提亲，你就出了事。"),
    ("character", "姑娘，这话从何说起？林某在东京那些年，未曾……你先起来。"),
    ("user", "我从怀里取出那半块玉佩，双手捧到你面前，玉上刻着半个「苏」字。"),
    ("character", "这玉……成色倒像是东京的老物。你娘是几时没的？"),
    ("user", "娘是三年前的冬天走的，咳血，走了一夜。葬在城西乱葬岗，我攒钱立了块薄石碑。"),
    ("character", "三年前……她竟已走了三年。"),
    ("user", "娘还说：你左肩胛上有一道旧疤，是那年替她挡刀留下的。"),
    ("character", "这道疤，林某从没对人讲过。"),
    ("user", "娘说你是因一口宝刀、白虎堂那桩冤案，被人算计了去。"),
    ("character", "宝刀、白虎堂——这两桩事，只有当事的人晓得。"),
    ("user", "女儿不求跟你走，只求你说一句认我的话。"),
    ("character", "玉娆，你把头抬起来。爹认你。"),
]

# 补到 30 条，把最早那几条挤出 24 条的窗口，前情提要才会真的出现在 prompt 里
FILLER = [
    ("user", "爹，这一路我在庙里歇脚，没敢进城。"),
    ("character", "城外风大，你夜里别宿在破庙，寻个人家借宿。"),
    ("user", "女儿晓得了。爹这几日往哪里去？"),
    ("character", "林某要往梁山去，路不好走，你不必跟着。"),
]

# 摘要不该出现的东西：出戏词、数值化关系、以及「用户」这种称呼
BAD_WORDS = ("人工智能", "语言模型", "提示词", "用户", "剧情", "好感", "信任度", "数值")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="F22 会话摘要真实模型验针")
    parser.add_argument("--http", metavar="BASE_URL", default=None, help="打已启动的服务")
    parser.add_argument("--timeout", type=float, default=120.0, help="超时（秒）")
    return parser.parse_args()


async def seed_session(client: httpx.AsyncClient) -> Dict[str, Any]:
    """建账号 / 身份 / 会话，并把现成对话直接写进库（不花额度让角色逐轮回复）。"""

    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"summary-{uuid.uuid4().hex[:8]}", "display_name": "玉娆"},
        )
    ).json()
    persona = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={
                "work_slug": WORK,
                "name": "玉娆",
                "identity": "林冲失散多年的私生女，随母姓",
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
                "character_slugs": ["lin-chong"],
            },
        )
    ).json()
    return {"user": user, "persona": persona, "session": session}


async def write_transcript(session_id: int) -> int:
    """直接把对话写进 messages（真实模型那一步只留给摘要）。返回写了几条。"""

    turns: List[tuple] = []
    while len(turns) < 30:
        turns.extend(CANNED)
        turns.extend(FILLER)
    turns = turns[:30]

    async with db.pool().acquire() as conn:
        session = await repo.get_session(conn, session_id)
        anchor = await repo.get_current_anchor(conn, session["user_id"], session["work_id"])
        character_id = await conn.fetchval(
            "SELECT id FROM characters WHERE slug = 'lin-chong'"
        )
        await repo.lock_session(conn, session_id)
        for index, (speaker, content) in enumerate(turns, start=1):
            await conn.execute(
                """
                INSERT INTO messages (session_id, seq, sender_kind, sender_id, message_kind,
                                      content, anchor_id)
                VALUES ($1, $2, $3, $4, 'text', $5, $6)
                """,
                session_id,
                index,
                speaker,
                session["user_id"] if speaker == "user" else character_id,
                content,
                anchor["id"],
            )
    return len(turns)


async def run(args: argparse.Namespace) -> int:
    provider = build_provider(settings)
    print(f"provider : {provider.name} / {provider.model}")
    print(f"场景     : 第{CHAPTER}回，现成对话 30 条（只花一次模型调用）")
    print("-" * 72)

    if provider.name == "mock":
        print("当前是离线 mock provider：这条探针要验的是真实模型写的提要，先退出。")
        return 0

    transport = None if args.http else httpx.ASGITransport(app=app)
    await db.connect()
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url=args.http or "http://summary", timeout=args.timeout
        ) as client:
            ctx = await seed_session(client)
            session_id = ctx["session"]["id"]
            written = await write_transcript(session_id)
            print(f"会话     : session={session_id}，写入 {written} 条对话")

            async with db.pool().acquire() as conn:
                session = await repo.get_session(conn, session_id)
                anchor = await repo.get_current_anchor(
                    conn, session["user_id"], session["work_id"]
                )
                members = await repo.list_session_members(conn, session_id)
                characters = await repo.get_characters_by_ids(
                    conn, [m["member_id"] for m in members if m["member_kind"] == "character"]
                )
                row = await refresh_anchor_summary(
                    conn,
                    session=session,
                    anchor=anchor,
                    responder=characters[0],
                    summarizer=build_summarizer(),
                )
            if row is None:
                print("✗ 摘要没写出来（模型没吐正文，或判成不该压）")
                return 1

            print(f"摘要     : 覆盖 seq {row['covered_from_seq']}~{row['covered_to_seq']}")
            print(f"         : {row['summary']}")

            problems = [f"提要里不该出现「{word}」" for word in BAD_WORDS if word in row["summary"]]
            if len(row["summary"]) > 300:
                problems.append(f"提要太长（{len(row['summary'])} 字）——要的是一到三句")

            preview = await client.get(f"/api/sessions/{session_id}/prompt-preview")
            preview.raise_for_status()
            prompt = preview.json()["system_prompt"]
            if "# 你和这个人更早说过的话" not in prompt:
                problems.append("prompt 里没有「更早说过的话」那一段")
            elif row["summary"] not in prompt:
                problems.append("prompt 里的提要与落库的不是同一条")

            print("-" * 72)
            if problems:
                for problem in problems:
                    print(f"✗ {problem}")
                return 1
            print("结果     : 通过——真实模型压出了提要，prompt 里也按前情提要给了")
            print(f"留库备查：session={session_id}（试验台可切到「玉娆」复看）")
    finally:
        await db.disconnect()
    return 0


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
