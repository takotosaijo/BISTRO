from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CreateUserRequest(BaseModel):
    external_id: str = Field(..., description="外部账号标识，便于对接登录体系")
    display_name: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    external_id: Optional[str] = None
    display_name: Optional[str] = None


class PersonaRequest(BaseModel):
    """建一个身份。一个账号在一个作品里可以有多个身份，见 DECISIONS 2026-09-16。"""

    work_slug: str
    name: str
    identity: Optional[str] = None
    background: Optional[str] = None
    appearance: Optional[str] = None
    speech_style: Optional[str] = None
    free_note: Optional[str] = None


class PersonaUpdateRequest(BaseModel):
    name: str
    identity: Optional[str] = None
    background: Optional[str] = None
    appearance: Optional[str] = None
    speech_style: Optional[str] = None
    free_note: Optional[str] = None


class SetTimelineRequest(BaseModel):
    work_slug: str
    anchor_seq: Optional[int] = Field(None, description="时间锚点序号（内部顺序）")
    chapter_no: Optional[int] = Field(None, description="章回号，前端滑块一般用这个")


class CreateSessionRequest(BaseModel):
    persona_id: int = Field(..., description="以哪个身份开口；账号由身份推出来")
    work_slug: str = "shuihu-100"
    session_type: str = Field("direct", description="direct 或 group")
    character_slugs: List[str]
    title: Optional[str] = None
    is_non_canon: bool = False


class SendMessageRequest(BaseModel):
    content: str
    responder_slug: Optional[str] = Field(
        None, description="群聊里指定由谁回话；1v1 可省略"
    )
    kind: str = Field(
        "text",
        description="text＝这个人说的话；narration＝这个人做的动作（F26 的点选项与自定义动作）",
    )


class SessionResponse(BaseModel):
    id: int
    session_type: str
    title: str
    is_non_canon: bool
    last_message_at: Optional[Any] = None
    member_names: Optional[List[str]] = None


class MessageResponse(BaseModel):
    id: int
    seq: int
    sender_kind: str
    sender_id: Optional[int] = None
    message_kind: str
    content: str
    anchor_id: Optional[int] = None
    created_at: Optional[Any] = None


class ChatResponse(BaseModel):
    user_message: Dict[str, Any]
    reply: Dict[str, Any]
    anchor: Dict[str, Any]
    responder: Dict[str, Any]
    provider: Dict[str, Any]
    relation_changes: List[Dict[str, Any]] = Field(
        default_factory=list, description="这一轮发生的关系变化（F14）"
    )
    action_options: List[Dict[str, Any]] = Field(
        default_factory=list, description="角色明确提出要求时给出的行动选项（F26，3~4 个）"
    )


class PromptOverrideRequest(BaseModel):
    """F23 四维度覆盖：四个维度列留空表示「不限」，填了才限定生效范围。"""

    work_slug: str
    key: str = Field(description="覆盖哪一格，例如「说话分寸」「额外交代」")
    body: str
    persona_id: Optional[int] = None
    character_id: Optional[int] = None
    anchor_seq: Optional[int] = Field(
        None, description="按时间线序号限定；同 key 命中多条时会话 > 锚点 > 角色 > 用户"
    )
    session_id: Optional[int] = None
    note: Optional[str] = Field(None, description="为什么加这条，给人看的")
