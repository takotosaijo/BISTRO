"""离线占位 provider。

不需要 API Key 也能跑通「装配 prompt → 生成 → 落库」的完整链路，
并且回复里会带上角色的当前处境，方便肉眼确认时间线确实生效了。
"""

from __future__ import annotations

import re
from typing import AsyncIterator, List, Optional

from app.providers.base import ChatMessage

_NAME_RE = re.compile(r"你是《[^》]+》中的([^（。\n]+)")


def _field(system_prompt: str, prefix: str) -> str:
    for line in system_prompt.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


class MockProvider:
    name = "mock"

    def __init__(self, model: str = "mock-1") -> None:
        self.model = model

    async def stream(
        self, messages: List[ChatMessage], *, temperature: Optional[float] = None
    ) -> AsyncIterator[str]:
        system = next((m.content for m in messages if m.role == "system"), "")
        user_text = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )

        match = _NAME_RE.search(system)
        name = match.group(1).strip() if match else "某人"
        location = _field(system, "你此刻在：")
        daily = _field(system, "你最近在做：")

        aside = ""
        if location or daily:
            aside = f"（{location}。{daily}）"

        reply = f"（{name}）{user_text}——这话我记下了。{aside}"
        # 按字符切片模拟流式输出
        for i in range(0, len(reply), 8):
            yield reply[i : i + 8]
