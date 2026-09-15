from __future__ import annotations

from typing import Dict, Optional

from app.config import Settings, settings as default_settings
from app.providers.base import ChatMessage, LLMProvider
from app.providers.mock import MockProvider
from app.providers.openai_compat import OpenAICompatProvider

__all__ = [
    "ChatMessage",
    "LLMProvider",
    "MockProvider",
    "OpenAICompatProvider",
    "OPENAI_COMPAT_PROVIDERS",
    "build_provider",
]

# 真实模型都走 OpenAI 兼容协议，差别只在默认地址。键名就是 BISTRO_LLM_PROVIDER 的取值。
OPENAI_COMPAT_PROVIDERS: Dict[str, str] = {
    "openai_compat": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
}

VALID_PROVIDERS = ("mock",) + tuple(OPENAI_COMPAT_PROVIDERS)


def build_provider(settings: Optional[Settings] = None) -> LLMProvider:
    """按配置挑选 provider。

    默认 mock，保证在没有 API Key、没有网络的环境里也能把整条链路跑通。

    配置写错时直接报错，不静默退回 mock——否则「以为接了真实模型、其实在跟
    mock 说话」这种事故只有对比回复语气才能发现。
    """

    cfg = settings or default_settings
    name = (cfg.llm_provider or "").strip().lower()

    if name == "mock":
        return MockProvider(model=cfg.llm_model)

    if name in OPENAI_COMPAT_PROVIDERS:
        return OpenAICompatProvider(
            base_url=cfg.llm_base_url or OPENAI_COMPAT_PROVIDERS[name],
            api_key=cfg.llm_api_key or "",
            model=cfg.llm_model,
            timeout=cfg.llm_timeout_seconds,
            thinking=cfg.llm_thinking,
            name=name,
        )

    raise ValueError(
        f"未知的 BISTRO_LLM_PROVIDER={cfg.llm_provider!r}，可选：{' / '.join(VALID_PROVIDERS)}"
    )
