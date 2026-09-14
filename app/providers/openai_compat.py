"""任何 OpenAI 兼容接口（OpenAI、DeepSeek、Qwen、自建 vLLM 等）。"""

from __future__ import annotations

import json
from typing import AsyncIterator, List, Optional

import httpx

from app.providers.base import ChatMessage


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(
        self, base_url: str, api_key: str, model: str, timeout: float = 60.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def stream(
        self, messages: List[ChatMessage], *, temperature: Optional[float] = None
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", json=payload, headers=headers
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
