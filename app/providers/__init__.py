from __future__ import annotations

from typing import Optional

from app.config import Settings, settings as default_settings
from app.providers.base import ChatMessage, LLMProvider
from app.providers.mock import MockProvider
from app.providers.openai_compat import OpenAICompatProvider

__all__ = ["ChatMessage", "LLMProvider", "MockProvider", "OpenAICompatProvider", "build_provider"]


def build_provider(settings: Optional[Settings] = None) -> LLMProvider:
    """按配置挑选 provider。

    默认 mock，保证在没有 API Key、没有网络的环境里也能把整条链路跑通。
    """

    cfg = settings or default_settings
    if cfg.llm_provider == "openai_compat":
        return OpenAICompatProvider(
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key or "",
            model=cfg.llm_model,
            timeout=cfg.llm_timeout_seconds,
        )
    return MockProvider(model=cfg.llm_model)
