"""真实模型冒烟测试：用当前 .env 的配置，真跑一轮对话。

    .venv/bin/python scripts/check_llm.py
    .venv/bin/python scripts/check_llm.py --chapter 71 --character wu-song \
        --message "兄弟，近来可好？"

它走的是和线上完全相同的一条路：建用户 → 拨时间线 → 开 1v1 会话 →
`/messages/stream`（SSE）→ 真实模型 → 落库。因此这一条命令同时验证了
「prompt 装配 → 真实模型 → 流式解析 → 落库」整条链路。

两种跑法：
  - 默认直接在进程内挂 ASGI（不依赖服务在跑），但 httpx 的 ASGI 传输层会
    缓冲整个响应，所以「首字延迟」这一项不准，只看是否出正文。
  - `--http http://127.0.0.1:8002` 打真实服务（`make run`），首字延迟是真的。

需要数据库可达 + 网络可达。provider=mock 时只打印配置并退出，不消耗额度。
退出码：0 正常 / 1 模型没吐出正文 / 2 调用出错。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.providers import build_provider  # noqa: E402

WORK = settings.default_work_slug


def mask(secret: Optional[str]) -> str:
    """只露头尾，够确认「用的是哪把 key」，又不至于把整串打进日志。"""

    if not secret:
        return "(未设置)"
    if len(secret) <= 12:
        return secret[:2] + "…"
    return f"{secret[:7]}…{secret[-4:]}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实模型冒烟测试")
    parser.add_argument("--chapter", type=int, default=10, help="章回号，默认 10（风雪山神庙）")
    parser.add_argument("--character", default="lin-chong", help="角色 slug，默认 lin-chong")
    parser.add_argument(
        "--message", default="这位大哥看着面生，从何处来？", help="用户说的话"
    )
    parser.add_argument(
        "--http",
        metavar="BASE_URL",
        default=None,
        help="打已启动的服务（如 http://127.0.0.1:8002），首字延迟才准确",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="整体超时（秒）")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    provider = build_provider(settings)  # 配置写错会在这里直接炸

    print(f"provider : {provider.name}")
    print(f"model    : {provider.model}")
    print(f"base_url : {getattr(provider, 'base_url', '(内置)')}")
    print(f"api_key  : {mask(settings.llm_api_key)}")
    print(f"thinking : {settings.llm_thinking}")

    if provider.name == "mock":
        print("\n当前是离线 mock provider，跳过真实调用。")
        print("接真实模型：在 .env 里把 BISTRO_LLM_PROVIDER 改成 deepseek 并填好密钥。")
        return 0

    # 打真实服务时数据库由服务自己管，脚本不需要再连一次
    transport = None if args.http else httpx.ASGITransport(app=app)
    base_url = args.http or "http://check"
    if transport:
        await db.connect()
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url=base_url, timeout=args.timeout
        ) as client:
            user = (
                await client.post(
                    "/api/users",
                    json={
                        "external_id": f"check-{uuid.uuid4().hex[:8]}",
                        "display_name": "冒烟测试用户",
                    },
                )
            ).json()
            persona = (
                await client.post(
                    f"/api/users/{user['id']}/personas",
                    json={
                        "work_slug": WORK,
                        "name": "张三",
                        "identity": "东京城里开酒铺的掌柜",
                        "speech_style": "客气里带点精明",
                    },
                )
            ).json()
            await client.put(
                f"/api/users/{user['id']}/timeline",
                json={"work_slug": WORK, "chapter_no": args.chapter},
            )
            session = (
                await client.post(
                    "/api/sessions",
                    json={
                        "persona_id": persona["id"],
                        "work_slug": WORK,
                        "session_type": "direct",
                        "character_slugs": [args.character],
                    },
                )
            ).json()

            streaming_is_real = bool(args.http)
            return await stream_turn(client, session["id"], args.message, streaming_is_real)
    finally:
        if transport:
            await db.disconnect()


async def stream_turn(
    client: httpx.AsyncClient, session_id: int, message: str, faithful_timing: bool
) -> int:
    print(f"session  : {session_id}")
    print(f"用户说   : {message}")
    print("-" * 60)

    started = time.perf_counter()
    first_delta: Optional[float] = None
    chunks: List[str] = []
    meta: Dict[str, Any] = {}
    event: Optional[str] = None
    error: Optional[str] = None

    async with client.stream(
        "POST",
        f"/api/sessions/{session_id}/messages/stream",
        json={"content": message},
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event = line[len("event: ") :].strip()
                continue
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            if event == "meta":
                meta = payload
            elif event == "delta":
                if first_delta is None:
                    first_delta = time.perf_counter()
                chunks.append(payload["text"])
            elif event == "error":
                error = payload.get("message", json.dumps(payload, ensure_ascii=False))

    elapsed = time.perf_counter() - started
    text = "".join(chunks).strip()

    if meta:
        anchor = meta.get("anchor", {})
        responder = meta.get("responder", {})
        print(f"时间点   : {anchor.get('chapter_label')} / {anchor.get('name')}")
        print(f"回应角色 : {responder.get('name')}（{responder.get('slug')}）")

    if error:
        print(f"调用出错 : {error}")
        return 2

    if first_delta and faithful_timing:
        print(f"首字延迟 : {first_delta - started:.2f}s")
    elif first_delta:
        print("首字延迟 : （ASGI 直连会缓冲响应，这项不准；加 --http 打真实服务才准）")
    else:
        print("首字延迟 : 无")
    print(f"总耗时   : {elapsed:.2f}s")
    print(f"回复     : {text or '(空)'}")

    if not text:
        print("\n失败：模型没有吐出任何正文。")
        print("若用的是推理模型，先看是不是思考把输出预算吃光了，或试试 BISTRO_LLM_THINKING=disabled。")
        return 1

    print("\n通过：真实模型链路可用。")
    return 0


def main() -> int:
    try:
        return asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本，打印原因比抛栈更有用
        print(f"\n调用失败：{type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
