from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """环境变量前缀统一为 BISTRO_，也支持写在 .env 里。"""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="BISTRO_", extra="ignore")

    database_url: str = "postgresql://postgres@127.0.0.1:5432/bistro"

    # mock 无需任何外部依赖即可跑通链路；openai_compat 适用于任何 OpenAI 兼容接口
    llm_provider: str = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: Optional[str] = None
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.85
    llm_timeout_seconds: float = 60.0

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
