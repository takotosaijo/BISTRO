"""任何 OpenAI 兼容接口（OpenAI、DeepSeek、Qwen、自建 vLLM 等）。"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.providers.base import ChatMessage


def extract_content(line: str) -> Optional[str]:
    """从一行 SSE 里取出正文增量；这一行不是正文就返回 None。

    跳过：非 `data:` 行、`[DONE]`、没有 choices 的心跳包，以及推理模型
    只带 `reasoning_content` 的思考增量（那不是角色要说的话）。
    """

    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        return None
    choices = chunk.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content or None


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        thinking: str = "auto",
        name: str = "openai_compat",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.thinking = (thinking or "auto").strip().lower()
        self.name = name

    def build_payload(
        self, messages: List[ChatMessage], temperature: Optional[float] = None
    ) -> Dict[str, Any]:
        """请求体单独抽出来，便于测试断言（尤其是 thinking 开关）。"""

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        # DeepSeek 的推理模型默认先吐一大段 reasoning_content 才吐正文，
        # 首字延迟成倍增长；thinking=disabled 可以关掉，详见 DECISIONS.md。
        if self.thinking == "disabled":
            payload["thinking"] = {"type": "disabled"}
        return payload

    async def stream(
        self, messages: List[ChatMessage], *, temperature: Optional[float] = None
    ) -> AsyncIterator[str]:
        payload = self.build_payload(messages, temperature)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", json=payload, headers=headers
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    content = extract_content(line)
                    if content:
                        yield content
