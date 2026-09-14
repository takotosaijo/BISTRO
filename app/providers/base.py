from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, List, Optional, Protocol, runtime_checkable


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    """模型层的统一入口。

    上层只依赖 stream()，因此换厂商、换模型、加缓存都不影响对话管线。
    """

    name: str
    model: str

    def stream(
        self, messages: List[ChatMessage], *, temperature: Optional[float] = None
    ) -> AsyncIterator[str]:
        ...
