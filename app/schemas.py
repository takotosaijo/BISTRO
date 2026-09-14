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
    work_slug: str
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
    user_id: int
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
