"""角色一致性评测（F21 缩水版）：拿真实模型跑探针，挑出不合规的回复。

    make eval                                        # 全部探针
    .venv/bin/python scripts/eval_characters.py --character lin-chong
    .venv/bin/python scripts/eval_characters.py --http http://127.0.0.1:8002
    .venv/bin/python scripts/eval_characters.py --json /tmp/eval.json   # 存一份结果

探针集在 evals/character_probes.json。每个探针 = 一次真实调用 + 一组**可机械判定**的检查：

  1. prompt_has_any：送给模型的 prompt 里必须出现这些词——时间线接没接上，这是确定性的
  2. 回复的负面检查：出戏词（AI / 模型 / 提示词 / 原著 / 剧本…）、现代词（手机 / 电脑 / 微信…）、
     别人的自称（林冲说「洒家」就是 OOC）、提前知道未来（按 evals/canon_facts.py 的章回表判定）
  3. expect_any：只用在「直接问、必须正面回答」的探针上——角色有权拒绝陌生人，别拿它当判据
  4. 长度：prompt 里写着「一到三句」，这里按字数兜底

它**不判**「像不像本人」——那是人看的。脚本只负责把明显违规的挑出来，
剩下的交给眼睛。需要网络与额度；provider=mock 时会警告（探针本来就是给真实模型的）。
退出码：0 全部通过 / 1 有不合规 / 2 调用出错。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.providers import build_provider  # noqa: E402
from evals.canon_facts import FUTURE_TERMS, OUT_OF_WORLD  # noqa: E402

WORK = settings.default_work_slug
PROBES_PATH = os.path.join(ROOT, "evals", "character_probes.json")

# 出戏：角色不该知道自己在跟程序打交道
META_WORDS = (
    "人工智能",
    "语言模型",
    "大模型",
    "模型",
    "扮演",
    "提示词",
    "系统提示",
    "助手",
    "机器人",
    "用户",
    "剧情",
    "剧本",
    "穿越",
)
# 现代词：宋元白话里不该有
MODERN_WORDS = ("手机", "电脑", "微信", "咖啡", "视频", "网络", "数据")
# prompt 的说话规矩是「一到三句」，这里按字数兜底
MAX_REPLY_CHARS = 140


def check_reply(
    case: Dict[str, Any],
    prompt: str,
    reply: str,
    voice: Dict[str, Any],
) -> List[str]:
    """返回不合规清单；空列表 = 通过。

    先查 prompt（时间线接不接地气是确定性的），再查回复（出戏、剧透、口吻）。
    """

    text = (reply or "").strip()
    if not text:
        return ["回复为空"]

    problems: List[str] = []

    prompt_has_any = case.get("prompt_has_any") or []
    if prompt_has_any and not any(word in prompt for word in prompt_has_any):
        problems.append("prompt 没接上时间线，缺：" + "、".join(prompt_has_any))

    if len(text) > MAX_REPLY_CHARS:
        problems.append(f"太长（{len(text)} 字 > {MAX_REPLY_CHARS}）")

    for word in META_WORDS:
        if word in text:
            problems.append(f"出戏词「{word}」")
    if re.search(r"(?<![A-Za-z])ai(?![A-Za-z])", text, re.IGNORECASE):
        problems.append("出戏词「AI」")
    for word in MODERN_WORDS:
        if word in text:
            problems.append(f"现代词「{word}」")
    for word in OUT_OF_WORLD:
        if word in text:
            problems.append(f"出戏词「{word}」")

    forbidden = list(dict.fromkeys(list(voice.get("forbidden") or []) + list(case.get("forbid") or [])))
    for word in forbidden:
        if word in text:
            problems.append(f"不该出现「{word}」")

    chapter_no = int(case["chapter_no"])
    for term, earliest in FUTURE_TERMS.get(case["slug"], {}).items():
        if chapter_no < earliest and term in text:
            problems.append(f"提前知道未来「{term}」（第{earliest}回才成立）")

    expect_any = case.get("expect_any") or []
    if expect_any and not any(word in text for word in expect_any):
        problems.append("没答在点上，缺：" + "、".join(expect_any))

    return problems


def load_probes() -> Dict[str, Any]:
    with open(PROBES_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="角色一致性评测")
    parser.add_argument("--character", action="append", default=[], help="只跑这些 slug，可重复")
    parser.add_argument("--http", metavar="BASE_URL", default=None, help="打已启动的服务")
    parser.add_argument("--timeout", type=float, default=120.0, help="单次调用超时（秒）")
    parser.add_argument("--json", dest="json_out", default=None, help="把结果写到这个文件")
    return parser.parse_args()


async def make_reader_persona(client: httpx.AsyncClient, chapter_no: int) -> int:
    """借一个身份来跑探针；时间线是账号级的，所以一个章节一个账号。"""

    response = await client.post(
        "/api/users",
        json={
            # 固定 id：反复跑评测时复用同一个用户，别把用户表撑爆
            "external_id": f"eval-chapter-{chapter_no}",
            "display_name": "评测用户",
        },
    )
    response.raise_for_status()
    user = response.json()

    persona = await client.post(
        f"/api/users/{user['id']}/personas",
        json={
            "work_slug": WORK,
            "name": "张三",
            "identity": "东京城里开酒铺的掌柜",
            "speech_style": "客气里带点精明",
        },
    )
    persona.raise_for_status()
    await client.put(
        f"/api/users/{user['id']}/timeline",
        json={"work_slug": WORK, "chapter_no": chapter_no},
    )
    return persona.json()["id"]


async def run_case(
    client: httpx.AsyncClient, case: Dict[str, Any], persona_id: int
) -> Dict[str, Any]:
    session_response = await client.post(
        "/api/sessions",
        json={
            "persona_id": persona_id,
            "work_slug": WORK,
            "session_type": "direct",
            "character_slugs": [case["slug"]],
        },
    )
    session_response.raise_for_status()
    session = session_response.json()

    # 先看这一轮将要送进模型的 prompt：时间线有没有接上，这一眼就能定
    preview = await client.get(f"/api/sessions/{session['id']}/prompt-preview")
    preview.raise_for_status()
    prompt = preview.json()["system_prompt"]

    reply_response = await client.post(
        f"/api/sessions/{session['id']}/messages", json={"content": case["probe"]}
    )
    reply_response.raise_for_status()
    body = reply_response.json()
    return {
        "slug": case["slug"],
        "chapter_no": case["chapter_no"],
        "probe": case["probe"],
        "why": case.get("why"),
        "prompt": prompt,
        "reply": body["reply"]["content"],
        "provider": f"{body['provider']['name']}:{body['provider']['model']}",
    }


async def run(args: argparse.Namespace) -> int:
    provider = build_provider(settings)
    probes = load_probes()
    cases = [c for c in probes["cases"] if not args.character or c["slug"] in args.character]
    if not cases:
        print("没有匹配的探针；--character 传的是不是 slug？")
        return 2

    print(f"provider : {provider.name} / {provider.model}")
    print(f"探针集   : {os.path.relpath(PROBES_PATH, ROOT)}（{len(cases)} 条）")
    if provider.name == "mock":
        print("警告     : 当前是离线 mock provider，探针本来就是给真实模型的，结果仅供参考")
    print("-" * 72)

    transport = None if args.http else httpx.ASGITransport(app=app)
    if transport is not None:
        await db.connect()

    results: List[Dict[str, Any]] = []
    failures = 0
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url=args.http or "http://eval", timeout=args.timeout
        ) as client:
            personas: Dict[int, int] = {}
            for index, case in enumerate(cases, start=1):
                chapter_no = int(case["chapter_no"])
                if chapter_no not in personas:
                    personas[chapter_no] = await make_reader_persona(client, chapter_no)
                result = await run_case(client, case, personas[chapter_no])
                problems = check_reply(
                    case,
                    result["prompt"],
                    result["reply"],
                    probes["voice"].get(case["slug"], {}),
                )
                result.pop("prompt")  # 结果里不必留整段 prompt
                result["problems"] = problems
                results.append(result)

                mark = "OK  " if not problems else "FAIL"
                label = f"{case['slug']}@第{chapter_no}回"
                print(f"[{index:2d}/{len(cases)}] {mark} {label}")
                print(f"        问：{case['probe']}")
                print(f"        答：{result['reply']}")
                for problem in problems:
                    print(f"        ✗ {problem}")
                    failures += 1
    finally:
        if transport is not None:
            await db.disconnect()

    passed = sum(1 for r in results if not r["problems"])
    print("-" * 72)
    print(f"结果：{passed}/{len(results)} 条探针通过")
    if failures:
        print("不合规汇总：")
        for result in results:
            for problem in result["problems"]:
                print(f"  - {result['slug']}@第{result['chapter_no']}回：{problem}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"summary": {"passed": passed, "total": len(results)}, "results": results},
                      handle, ensure_ascii=False, indent=2)
        print(f"结果已写入 {args.json_out}")

    return 1 if failures else 0


def main() -> int:
    try:
        return asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - 评测脚本，打印原因比抛栈有用
        print(f"\n评测中断：{type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
