from __future__ import annotations

from typing import Optional


class DomainError(Exception):
    """业务错误。路由层统一转成 HTTP 响应。"""

    def __init__(self, message: str, status_code: int = 400, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code or "domain_error"


class NotFound(DomainError):
    def __init__(self, message: str = "对象不存在"):
        super().__init__(message, status_code=404, code="not_found")


class CharacterUnavailable(DomainError):
    """该角色在此时间锚点不可对话（已亡故或不应提前唤醒）。"""

    def __init__(self, message: str):
        super().__init__(message, status_code=409, code="character_unavailable")
