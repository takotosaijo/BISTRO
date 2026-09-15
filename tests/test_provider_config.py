"""模型层配置测试：provider 选择、DeepSeek 默认地址、thinking 开关、SSE 解析。

不发真实请求，只钉住「配置怎么解析」与「报文怎么解析」这两处最容易配错的地方。
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.providers import build_provider
from app.providers.base import ChatMessage
from app.providers.mock import MockProvider
from app.providers.openai_compat import OpenAICompatProvider, extract_content

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


def _settings(**overrides) -> Settings:
    base = {"llm_provider": "mock", "llm_model": "gpt-4o-mini"}
    base.update(overrides)
    return Settings(**base)


def test_default_provider_is_mock() -> None:
    assert isinstance(build_provider(_settings()), MockProvider)


def test_deepseek_provider_uses_official_base_url() -> None:
    """只写 provider=deepseek 就该连到 DeepSeek，不用再手填 base_url。"""

    provider = build_provider(
        _settings(
            llm_provider="deepseek",
            llm_api_key="sk-test",
            llm_model="deepseek-v4-flash",
        )
    )
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.name == "deepseek"
    assert provider.base_url == DEEPSEEK_BASE_URL
    assert provider.model == "deepseek-v4-flash"
    assert provider.api_key == "sk-test"


def test_explicit_base_url_wins() -> None:
    """自建网关场景：显式给的 base_url 覆盖官方默认地址。"""

    provider = build_provider(
        _settings(llm_provider="deepseek", llm_base_url="http://127.0.0.1:9999/v1/")
    )
    assert provider.base_url == "http://127.0.0.1:9999/v1"  # 顺手去掉尾部斜杠


def test_unknown_provider_raises_instead_of_falling_back_to_mock() -> None:
    """provider 名写错必须炸，不能悄悄用 mock 假装接了真实模型。"""

    with pytest.raises(ValueError, match="BISTRO_LLM_PROVIDER"):
        build_provider(_settings(llm_provider="depseek"))


def test_thinking_disabled_is_sent_only_when_asked() -> None:
    messages = [ChatMessage(role="user", content="在？")]

    auto = build_provider(_settings(llm_provider="deepseek", llm_thinking="auto"))
    assert "thinking" not in auto.build_payload(messages, 0.85)

    off = build_provider(_settings(llm_provider="deepseek", llm_thinking="disabled"))
    payload = off.build_payload(messages, 0.85)
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["temperature"] == 0.85
    assert payload["stream"] is True
    assert payload["messages"] == [{"role": "user", "content": "在？"}]


def test_payload_omits_temperature_when_not_given() -> None:
    provider = build_provider(_settings(llm_provider="deepseek"))
    payload = provider.build_payload([ChatMessage(role="system", content="你是林冲")])
    assert "temperature" not in payload, "没传温度时不要凭空补一个"
    assert json.loads(json.dumps(payload))["model"] == "gpt-4o-mini"


# 以下三行是 2026-09-15 从 DeepSeek 真实 SSE 响应里抓下来的原始报文
REASONING_LINE = (
    'data: {"id":"x","choices":[{"index":0,"delta":'
    '{"content":null,"reasoning_content":"用户"},"finish_reason":null}]}'
)
CONTENT_LINE = (
    'data: {"id":"x","choices":[{"index":0,"delta":'
    '{"content":"在","reasoning_content":null},"finish_reason":null}]}'
)
STOP_LINE = (
    'data: {"id":"x","choices":[{"index":0,"delta":'
    '{"content":"","reasoning_content":null},"finish_reason":"stop"}],'
    '"usage":{"completion_tokens":156}}'
)


def test_extract_content_skips_reasoning_and_control_lines() -> None:
    assert extract_content(REASONING_LINE) is None, "思考过程不能混进角色回复"
    assert extract_content(CONTENT_LINE) == "在"
    assert extract_content(STOP_LINE) is None
    assert extract_content("data: [DONE]") is None
    assert extract_content("") is None
    assert extract_content("event: delta") is None
    assert extract_content('data: {"choices":[]}') is None
    assert extract_content("data: {坏掉的 json") is None
