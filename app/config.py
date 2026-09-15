from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """环境变量前缀统一为 BISTRO_，也支持写在 .env 里。"""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="BISTRO_", extra="ignore")

    database_url: str = "postgresql://postgres@127.0.0.1:5432/bistro"

    # mock 无需任何外部依赖即可跑通链路；
    # deepseek / openai_compat 都是 OpenAI 兼容接口，只差默认 base_url
    llm_provider: str = "mock"
    # 留空表示用该 provider 的官方地址（见 app/providers/__init__.py）
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.85
    llm_timeout_seconds: float = 60.0
    # 推理模型（deepseek-v4-flash / v4-pro）是否允许先思考：
    #   auto     交给模型自己决定（默认，角色质量优先）
    #   disabled 关掉思考，首字延迟显著下降（角色扮演的对话体验优先）
    llm_thinking: str = "auto"

    # 送进 prompt 的最近消息条数（不含本轮）
    max_history_messages: int = 24

    default_work_slug: str = "shuihu-100"

    @property
    def asyncpg_dsn(self) -> str:
        """asyncpg 不认 SQLAlchemy 的驱动前缀，顺手做一次归一化。"""

        dsn = self.database_url
        for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
            if dsn.startswith(prefix):
                dsn = "postgresql://" + dsn[len(prefix) :]
        return dsn


settings = Settings()
