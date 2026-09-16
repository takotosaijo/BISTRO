"""F14 真实模型正向验针：喂够凭证，逼出「他认下 → 关系写入新版本」。

    make eval-relation
    .venv/bin/python scripts/check_relation_evolution.py
    .venv/bin/python scripts/check_relation_evolution.py --http http://127.0.0.1:8002

为什么单独一条命令：F14 的**机制**已经被 mock 测通（`tests/test_relation_evolution.py`），
但「真实模型读了这一轮对话、判定发生了关系变化、代码照着写新边」这条端到端一直没跑出
正向案例——两次试探林冲都在要凭证（「玉佩拿来我看」）。

这条探针把凭证给足（娘亲留下的半块玉佩 + 只有家里人知道的身体特征 + 母亲遗言 +
答出他反复考问的旧案），然后机械地检查五件事：
  ① 变化检测器（真实模型）判定发生了 character_to_user 的关系变化，且 `kind == "recognition"`
     （**不抠 label 的字眼**：试过按关键词猜，把「却仍不肯当面认下这个女儿」当成了认下）
  ② `relationship_edges` 多出一个版本：旧边 `valid_to` = 第十回，新边 `valid_from` = 第十回
  ③ `relationship_changes` 留下审计（方向 / 前后标签 / 锚点 / 触发消息 / 原因）
  ④ prompt 里新说法生效、旧说法消失
  ⑤ 滑到第七十一回仍在、滑回第二回自动回到旧说法（回退是数据算出来的）

需要数据库可达 + 网络可达，会真实消耗额度。provider=mock 时直接退出——
这条探针的判据依赖真实模型的判定，mock 的结果证明不了任何东西。
退出码：0 通过 / 1 没跑出「认下」/ 2 调用出错。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.providers import build_provider  # noqa: E402

WORK = settings.default_work_slug
OPENING_CHAPTER = 10
AFTER_CHAPTER = 71
BEFORE_CHAPTER = 2

# 剧本：先认亲 → 把玉佩递到他手上 → 说出只有家里人知道的旧疤 → **回答他追问的**
# （娘几时没的、坟在何处）→ 答出那桩他反复考问的旧案 → 拆掉「怕连累你」这个理由，
# 只求他当着玉说一句。四条经验都来自真实模型的试探：① 林冲这种人不见凭证不认人，凭证要真的递到手上，
# 不能只说「我有」；② 他会一路考问（娘几时没的、坟在何处、当年那桩事因何而起），
# 不回答他就不往下走；③ 他会用「我是刺配的罪囚，认了是连累你」把人往外推，
# 得由这个人自己把「我不怕」说死；④ 光动情没用，得直接要那一句。
# 白虎堂那桩事在第七回就发生了，第十回的他本来就知道，
# 女儿答得出来不算剧透。
# 娘亲用苏氏——与 `make admin` 的「苏娘（林冲在东京时的旧相识）」对得上，
# 这样「私生女」这条线在项目自己的样本里是自洽的。
DEFAULT_TURNS = [
    "爹，女儿玉娆给你磕头。我娘是苏氏，东京人氏——当年与你未及提亲，你就出了事。"
    "娘临终前让我来寻你，说无论如何要找到你。",
    "女儿知道空口无凭，不敢乱认。我从怀里取出那半块玉佩，双手捧到你面前——"
    "玉上刻着半个「苏」字，断口是斜的。娘说另外半块当年你带走了，两块合起来才是一个整字。",
    "娘还说：你左肩胛上有一道旧疤，是那年替她挡刀留下的，这道疤除了家里人谁也不知道。"
    "她临终前只留下一句话——玉娆若是寻到你爹，替娘说一声，她没等错人。",
    "爹问娘几时没的、坟在何处——娘是三年前的冬天走的，咳血，走了一夜。"
    "葬在东京城西乱葬岗，女儿攒了两年钱，才给她立了一块薄石碑。",
    "爹问娘跟我讲过当年那桩事没有——讲过的。娘说爹在东京是八十万禁军枪棒教头，"
    "后来因一口宝刀、白虎堂那桩冤案，被人算计了去，刺配沧州。娘每回说到这里就哭，"
    "只说爹是冤枉的，这一句她到死都记着。",
    "爹是怕认了女儿，反倒给女儿招祸——可女儿早就是罪臣的女儿了：娘死那年，"
    "邻里就都知道我是谁家的骨肉；官府要查，认不认都查得着。女儿一个人被娘拉扯到十六岁，"
    "什么苦都经过，不怕连累，只怕这辈子连一声「爹」都没叫出口。"
    "爹就当着这块玉说一句认我的话——说完女儿立刻就走，绝不再来。",
]

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="F14 关系演化真实模型正向验针")
    parser.add_argument("--chapter", type=int, default=OPENING_CHAPTER, help="首次相认的章回，默认 10")
    parser.add_argument("--character", default="lin-chong", help="角色 slug，默认 lin-chong")
    parser.add_argument(
        "--message",
        action="append",
        default=[],
        help="覆盖默认剧本，可重复；不传跑内置的四轮",
    )
    parser.add_argument(
        "--http",
        metavar="BASE_URL",
        default=None,
        help="打已启动的服务（如 http://127.0.0.1:8002）；默认进程内直挂 ASGI",
    )
    parser.add_argument(
        "--allow-mock",
        action="store_true",
        help="provider=mock 时也往下跑（只验脚本管路，不算 F14 的真实验收）",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="单次调用超时（秒）")
    return parser.parse_args()


async def setup(
    client: httpx.AsyncClient, chapter_no: int, character_slug: str
) -> Dict[str, Any]:
    """建一个「玉娆」身份：人设里写清楚她为什么该被认（F12 会先解析出初始关系）。"""

    user_response = await client.post(
        "/api/users",
        json={"external_id": f"evolve-{uuid.uuid4().hex[:8]}", "display_name": "玉娆"},
    )
    user_response.raise_for_status()
    user = user_response.json()
    persona_response = await client.post(
        f"/api/users/{user['id']}/personas",
        json={
            "work_slug": WORK,
            "name": "玉娆",
            "identity": "林冲失散多年的私生女，随母姓",
            "background": (
                "娘亲苏氏是东京人氏，当年与爹未及提亲爹就出了事；她临终前把半块玉佩交给我，"
                "说玉上刻着半个「苏」字，另外半块当年爹带走了；"
                "她还说爹左肩胛上有一道旧疤，是替她挡刀留下的；"
                "爹在东京是八十万禁军枪棒教头，后来因一口宝刀、白虎堂那桩冤案被人算计了去。"
            ),
        },
    )
    persona_response.raise_for_status()
    persona = persona_response.json()

    await client.put(
        f"/api/users/{user['id']}/timeline", json={"work_slug": WORK, "chapter_no": chapter_no}
    )
    session_response = await client.post(
        "/api/sessions",
        json={
            "persona_id": persona["id"],
            "work_slug": WORK,
            "session_type": "direct",
            "character_slugs": [character_slug],
        },
    )
    session_response.raise_for_status()
    return {"user": user, "persona": persona, "session": session_response.json()}


async def prompt_of(client: httpx.AsyncClient, session_id: int) -> str:
    response = await client.get(f"/api/sessions/{session_id}/prompt-preview")
    response.raise_for_status()
    return response.json()["system_prompt"]


async def move_timeline(client: httpx.AsyncClient, user_id: int, chapter_no: int) -> None:
    response = await client.put(
        f"/api/users/{user_id}/timeline", json={"work_slug": WORK, "chapter_no": chapter_no}
    )
    response.raise_for_status()


async def fetch_character_id(slug: str) -> int:
    async with db.pool().acquire() as conn:
        return await conn.fetchval(
            """
            SELECT c.id FROM characters c
            JOIN works w ON w.id = c.work_id
            WHERE w.slug = $1 AND c.slug = $2
            """,
            WORK,
            slug,
        )


async def fetch_anchor_seq(chapter_no: int) -> int:
    async with db.pool().acquire() as conn:
        return await conn.fetchval(
            """
            SELECT a.seq FROM timeline_anchors a
            JOIN works w ON w.id = a.work_id
            WHERE w.slug = $1 AND a.chapter_no = $2
            """,
            WORK,
            chapter_no,
        )


async def fetch_edge_versions(persona_id: int, character_id: int) -> List[Dict[str, Any]]:
    """角色→用户这个方向的所有边版本，带生效区间的锚点顺序。"""

    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT e.id, e.label, vf.seq AS from_seq, vt.seq AS to_seq, e.valid_from_anchor_id
            FROM relationship_edges e
            LEFT JOIN timeline_anchors vf ON vf.id = e.valid_from_anchor_id
            LEFT JOIN timeline_anchors vt ON vt.id = e.valid_to_anchor_id
            WHERE e.source = 'user' AND e.persona_id = $1
              AND e.from_kind = 'character' AND e.from_id = $2
            ORDER BY e.id
            """,
            persona_id,
            character_id,
        )
        return [dict(row) for row in rows]


async def fetch_audit(persona_id: int) -> List[Dict[str, Any]]:
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ch.direction, ch.label_before, ch.label_after, ch.reason,
                   ch.message_id, a.seq AS anchor_seq
            FROM relationship_changes ch
            LEFT JOIN timeline_anchors a ON a.id = ch.anchor_id
            WHERE ch.persona_id = $1
            ORDER BY ch.id
            """,
            persona_id,
        )
        return [dict(row) for row in rows]


def fail(problems: List[str], message: str) -> None:
    problems.append(message)
    print(f"  ✗ {message}")


async def run(args: argparse.Namespace) -> int:
    provider = build_provider(settings)
    turns = args.message or DEFAULT_TURNS

    print(f"provider : {provider.name} / {provider.model}")
    print(f"角色     : {args.character} @ 第{args.chapter}回 → 第七十一回 → 第二回")
    print(f"剧本     : {len(turns)} 轮（先认亲、再给实物凭证、最后给只有家里人知道的细节）")
    print("-" * 72)

    if provider.name == "mock" and not args.allow_mock:
        print("当前是离线 mock provider：这条探针要验的是真实模型的判定，先退出。")
        print("接真实模型：在 .env 里把 BISTRO_LLM_PROVIDER 改成 deepseek 并填好密钥。")
        return 0
    mock_only = provider.name == "mock"
    if mock_only:
        print("警告     : 正在用 mock 跑——只验脚本管路，**不算** F14 的真实模型验收")

    transport = None if args.http else httpx.ASGITransport(app=app)
    await db.connect()
    problems: List[str] = []
    accepted_label: Optional[str] = None
    seen_options: List[Dict[str, str]] = []
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url=args.http or "http://evolve", timeout=args.timeout
        ) as client:
            ctx = await setup(client, args.chapter, args.character)
            persona, session, user = ctx["persona"], ctx["session"], ctx["user"]
            print(f"身份     : persona={persona['id']} / session={session['id']}")
            declared = persona.get("relations") or []
            print(
                "初始声明 : "
                + ("；".join(f"用户→{r['character_name']}：{r['user_label']}" for r in declared) or "（无）")
            )

            before_prompt = await prompt_of(client, session["id"])
            print("-" * 72)

            for index, text in enumerate(turns, start=1):
                response = await client.post(
                    f"/api/sessions/{session['id']}/messages", json={"content": text}
                )
                response.raise_for_status()
                body = response.json()
                reply = body["reply"]["content"]
                changes = body.get("relation_changes") or []
                options = body.get("action_options") or []
                print(f"[{index}/{len(turns)}] 用户：{text}")
                print(f"         林冲：{reply}")
                for change in changes:
                    print(
                        f"         关系变化：{change['direction']} / {change.get('kind')} "
                        f"「{change['label_before']}」→「{change['label_after']}」"
                    )
                if options:
                    print("         行动选项（F26）：")
                    for option in options:
                        print(f"           · {option['label']}｜{option['action']}")
                    if not 3 <= len(options) <= 4:
                        fail(problems, f"选项数量不是 3~4：{len(options)}")
                    if any(not o.get("label") or not o.get("action") for o in options):
                        fail(problems, "选项缺 label 或 action")
                    seen_options.extend(options)
                hit = [
                    change
                    for change in changes
                    if change["direction"] == "character_to_user"
                    and change.get("kind") == "recognition"
                ]
                if hit:
                    accepted_label = hit[0]["label_after"]
                    print(f"         ✓ 他认下了：{accepted_label}")
                    break
                if changes:
                    kinds = "、".join(str(change.get("kind")) for change in changes)
                    print(f"         · 写下了关系变化，但 kind={kinds}，不是 recognition——继续喂凭证")

            print("-" * 72)
            if not accepted_label:
                fail(
                    problems,
                    f"跑了 {len(turns)} 轮也没逼出 kind=recognition 的「认下」——"
                    "要么剧本里的凭证还不够，要么他一直在推后（deferral）",
                )
                return 1

            character_id = await fetch_character_id(args.character)
            opening_seq = await fetch_anchor_seq(args.chapter)
            versions = await fetch_edge_versions(persona["id"], character_id)
            print("边版本   :")
            for version in versions:
                print(
                    f"  - seq[{version['from_seq']}→{version['to_seq']}] {version['label']}"
                )

            old_versions = [v for v in versions if v["from_seq"] is None]
            # 「拒认」也会写版本（F14 两种都支持），所以新版要挑**当前生效**的那一条：
            # 从本章开始、且没有被闭口。中间被顶掉的那一版是零宽区间，忽略。
            new_versions = [
                v for v in versions if v["from_seq"] == opening_seq and v["to_seq"] is None
            ]
            if not old_versions:
                fail(problems, "找不到被闭口的旧版本（valid_from 为空的那一条）")
            elif old_versions[0]["to_seq"] != opening_seq:
                fail(
                    problems,
                    f"旧版本没在第{args.chapter}回闭口，valid_to seq={old_versions[0]['to_seq']}",
                )
            if not new_versions:
                fail(problems, f"没写出从第{args.chapter}回开始生效的新版本")
            elif new_versions[0]["label"] != accepted_label:
                fail(
                    problems,
                    f"新版标签与变化检测器给的不一致：{new_versions[0]['label']} ≠ {accepted_label}",
                )

            audit = await fetch_audit(persona["id"])
            if not audit:
                fail(problems, "relationship_changes 里没有审计")
            else:
                last = audit[-1]
                print(
                    f"审计     : {last['direction']} 锚点seq={last['anchor_seq']} "
                    f"「{last['label_before']}」→「{last['label_after']}」 原因：{last['reason']}"
                )
                if last["direction"] != "character_to_user":
                    fail(problems, f"审计方向不对：{last['direction']}")
                if last["anchor_seq"] != opening_seq:
                    fail(problems, f"审计锚点不是第{args.chapter}回：seq={last['anchor_seq']}")
                if not last["reason"]:
                    fail(problems, "审计没写原因，回答不了「他为什么改口」")

            after_prompt = await prompt_of(client, session["id"])
            if accepted_label not in after_prompt:
                fail(problems, "相认之后的 prompt 里没有新说法")
            if accepted_label in before_prompt:
                fail(problems, "相认之前的 prompt 里就已经有新说法")

            await move_timeline(client, user["id"], AFTER_CHAPTER)
            forward_prompt = await prompt_of(client, session["id"])
            if accepted_label not in forward_prompt:
                fail(problems, "滑到第七十一回之后新说法丢了")

            await move_timeline(client, user["id"], BEFORE_CHAPTER)
            back_prompt = await prompt_of(client, session["id"])
            if accepted_label in back_prompt:
                fail(problems, "滑回第二回之后新说法还在（回退失效）")
            if old_versions and old_versions[0]["label"] not in back_prompt:
                fail(problems, "滑回第二回之后旧说法没自动生效")

            print("-" * 72)
            print(f"留库备查：persona={persona['id']} session={session['id']}（试验台可切到这个身份复看）")
            print(f"情境选项 : 全程给出 {len(seen_options)} 条（他明确提要求时才给）")
    finally:
        await db.disconnect()

    if problems:
        print(f"结果     : 失败（{len(problems)} 项）")
        return 1
    if mock_only:
        print("结果     : 脚本管路通过（mock）——F14 的真实验收还没做，别标 passing")
        return 0
    print("结果     : 通过——真实模型认下 → 写入新边版本 → 按锚点可回退，逐项都对上了")
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
