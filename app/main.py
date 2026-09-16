from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, Query, Request
from fastapi import BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app import db
from app import repository as repo
from app.config import settings
from app.errors import DomainError, NotFound
from app.providers import build_provider
from app.schemas import (
    ChatResponse,
    CreateSessionRequest,
    CreateUserRequest,
    PersonaRequest,
    PersonaUpdateRequest,
    SendMessageRequest,
    SetTimelineRequest,
)
from app.services import chat as chat_service
from app.services.relation_parse import sync_declared_relations
from app.services.relation_evolve import evolve_relations

LAB_PAGE = Path(__file__).resolve().parent / "static" / "lab.html"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await db.connect()
    try:
        yield
    finally:
        await db.disconnect()


app = FastAPI(title="水浒角色对话系统", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"code": exc.code, "message": exc.message}
    )


def _provider():
    return build_provider(settings)


# ---------------------------------------------------------------------------
# 基础信息
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
    }


@app.get("/", include_in_schema=False)
async def lab_page() -> FileResponse:
    """试验台页面：单文件 HTML，服务直接托管。

    目的是把「拨时间线 → 同一个人换一套处境」变成可以用手拨的东西，
    而不是只能靠 /docs 和 curl 感受。不是产品前端（F19 正式形态还没定），
    所以不引构建工具、不做路由，随手可扔。
    """

    return FileResponse(LAB_PAGE, media_type="text/html")


@app.get("/api/works")
async def list_works() -> List[Dict[str, Any]]:
    async with db.pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, slug, title, author, edition, total_chapters FROM works ORDER BY id"
        )
    return [dict(r) for r in rows]


@app.get("/api/works/{work_slug}/anchors")
async def list_anchors(work_slug: str) -> List[Dict[str, Any]]:
    async with db.pool().acquire() as conn:
        work = await repo.get_work_by_slug(conn, work_slug)
        if work is None:
            raise NotFound("作品不存在")
        return await repo.list_anchors(conn, work["id"])


@app.get("/api/works/{work_slug}/characters")
async def list_characters(
    work_slug: str, persona_id: Optional[int] = Query(None)
) -> List[Dict[str, Any]]:
    """带上 persona_id 时，同时返回该身份所属账号当前时间点下的角色状态与可用性。

    时间线是账号级的（世界时间同一个），所以这里用身份反查账号，再取当前锚点。
    """

    async with db.pool().acquire() as conn:
        work = await repo.get_work_by_slug(conn, work_slug)
        if work is None:
            raise NotFound("作品不存在")
        characters = await repo.list_characters(conn, work["id"])
        if persona_id is None:
            return characters

        persona = await repo.get_persona(conn, persona_id)
        if persona is None:
            raise NotFound("身份不存在")
        anchor = await repo.get_current_anchor(conn, persona["user_id"], work["id"])
        states = await repo.get_character_states(
            conn, anchor["id"], [c["id"] for c in characters]
        ) if anchor else {}
        allow_early = bool(anchor and anchor.get("allow_early_characters"))
        for c in characters:
            state = states.get(c["id"])
            c["state_at_anchor"] = state
            availability = (state or {}).get("availability")
            c["selectable"] = availability not in ("deceased", "hidden") or allow_early
        return characters


# ---------------------------------------------------------------------------
# 用户、人设、时间线
# ---------------------------------------------------------------------------


@app.post("/api/users")
async def create_user(payload: CreateUserRequest) -> Dict[str, Any]:
    async with db.pool().acquire() as conn:
        return await repo.ensure_user(conn, payload.external_id, payload.display_name)


@app.post("/api/users/{user_id}/personas")
async def create_persona(user_id: int, payload: PersonaRequest) -> Dict[str, Any]:
    """给这个账号建一个身份。一个账号可以有很多个（玉娆 / 苏娘 / 路人张三）。"""

    async with db.pool().acquire() as conn:
        work = await repo.get_work_by_slug(conn, payload.work_slug)
        if work is None:
            raise NotFound("作品不存在")
        persona = await repo.create_persona(
            conn,
            user_id,
            work["id"],
            name=payload.name,
            identity=payload.identity,
            background=payload.background,
            appearance=payload.appearance,
            speech_style=payload.speech_style,
            free_note=payload.free_note,
        )
        persona["relations"] = await sync_declared_relations(conn, persona)
        return persona


@app.get("/api/users/{user_id}/personas")
async def list_personas(
    user_id: int, work_slug: str = Query("shuihu-100")
) -> List[Dict[str, Any]]:
    """这个账号在当前作品里的全部身份——试验台的「我的角色」列表就是它。"""

    async with db.pool().acquire() as conn:
        work = await repo.get_work_by_slug(conn, work_slug)
        if work is None:
            raise NotFound("作品不存在")
        return await repo.list_personas(conn, user_id, work["id"])


@app.get("/api/personas/{persona_id}")
async def get_persona(persona_id: int) -> Dict[str, Any]:
    async with db.pool().acquire() as conn:
        persona = await repo.get_persona(conn, persona_id)
        if persona is None:
            raise NotFound("身份不存在")
        persona["relations"] = await repo.list_persona_declarations(conn, persona_id)
        return persona


@app.put("/api/personas/{persona_id}")
async def update_persona(persona_id: int, payload: PersonaUpdateRequest) -> Dict[str, Any]:
    async with db.pool().acquire() as conn:
        persona = await repo.update_persona(
            conn,
            persona_id,
            name=payload.name,
            identity=payload.identity,
            background=payload.background,
            appearance=payload.appearance,
            speech_style=payload.speech_style,
            free_note=payload.free_note,
        )
        if persona is None:
            raise NotFound("身份不存在")
        persona["relations"] = await sync_declared_relations(conn, persona)
        return persona




@app.get("/api/users/{user_id}/timeline")
async def get_timeline(user_id: int, work_slug: str = Query("shuihu-100")) -> Dict[str, Any]:
    async with db.pool().acquire() as conn:
        work = await repo.get_work_by_slug(conn, work_slug)
        if work is None:
            raise NotFound("作品不存在")
        current = await repo.get_current_anchor(conn, user_id, work["id"])
        return {"work": work["slug"], "current": current}


@app.put("/api/users/{user_id}/timeline")
async def set_timeline(user_id: int, payload: SetTimelineRequest) -> Dict[str, Any]:
    if payload.anchor_seq is None and payload.chapter_no is None:
        raise DomainError("anchor_seq 与 chapter_no 至少要给一个")
    async with db.pool().acquire() as conn:
        work = await repo.get_work_by_slug(conn, payload.work_slug)
        if work is None:
            raise NotFound("作品不存在")
        if payload.anchor_seq is not None:
            anchor = await conn.fetchrow(
                "SELECT id FROM timeline_anchors WHERE work_id = $1 AND seq = $2",
                work["id"],
                payload.anchor_seq,
            )
        else:
            anchor = await conn.fetchrow(
                "SELECT id FROM timeline_anchors WHERE work_id = $1 AND chapter_no = $2",
                work["id"],
                payload.chapter_no,
            )
        if anchor is None:
            raise NotFound("时间锚点不存在")
        await repo.set_current_anchor(conn, user_id, work["id"], anchor["id"])
        return {
            "ok": True,
            "current": await repo.get_current_anchor(conn, user_id, work["id"]),
        }


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


@app.post("/api/sessions")
async def create_session(payload: CreateSessionRequest) -> Dict[str, Any]:
    if payload.session_type not in ("direct", "group"):
        raise DomainError("session_type 只能是 direct 或 group")
    if payload.session_type == "direct" and len(payload.character_slugs) != 1:
        raise DomainError("一对一聊天只能选一个角色")
    if not payload.character_slugs:
        raise DomainError("至少要选一个角色")

    async with db.pool().acquire() as conn:
        work = await repo.get_work_by_slug(conn, payload.work_slug)
        if work is None:
            raise NotFound("作品不存在")
        characters = await repo.get_characters_by_slugs(conn, work["id"], payload.character_slugs)
        found = {c["slug"] for c in characters}
        missing = [s for s in payload.character_slugs if s not in found]
        if missing:
            raise NotFound(f"角色不存在：{'、'.join(missing)}")

        persona = await repo.get_persona(conn, payload.persona_id)
        if persona is None:
            raise NotFound("身份不存在")
        anchor = await repo.get_current_anchor(conn, persona["user_id"], work["id"])
        title = payload.title or "、".join(c["name"] for c in characters)
        session = await repo.create_session(
            conn,
            persona_id=payload.persona_id,
            work_id=work["id"],
            session_type=payload.session_type,
            title=title,
            character_ids=[c["id"] for c in characters],
            created_anchor_id=anchor["id"] if anchor else None,
            is_non_canon=payload.is_non_canon,
        )
        session["members"] = [{"slug": c["slug"], "name": c["name"]} for c in characters]
        return session


@app.get("/api/sessions")
async def list_sessions(
    persona_id: int, work_slug: Optional[str] = Query(None)
) -> List[Dict[str, Any]]:
    async with db.pool().acquire() as conn:
        work_id = None
        if work_slug:
            work = await repo.get_work_by_slug(conn, work_slug)
            work_id = work["id"] if work else None
        return await repo.list_sessions(conn, persona_id, work_id)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: int) -> Dict[str, Any]:
    async with db.pool().acquire() as conn:
        session = await repo.get_session(conn, session_id)
        if session is None:
            raise NotFound("会话不存在")
        session["members"] = await repo.list_session_members(conn, session_id)
        return session


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: int, limit: int = Query(50, ge=1, le=500)) -> List[Dict[str, Any]]:
    async with db.pool().acquire() as conn:
        if await repo.get_session(conn, session_id) is None:
            raise NotFound("会话不存在")
        return await repo.list_messages(conn, session_id, limit)


@app.get("/api/sessions/{session_id}/prompt-preview")
async def prompt_preview(
    session_id: int, responder: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """把将要发给模型的 system prompt 原样吐回来，方便核对角色是否被写歪。

    这是调角色最重要的一个接口：不看它，等于闭着眼睛改 prompt。
    """

    async with db.pool().acquire() as conn:
        session = await repo.get_session(conn, session_id)
        if session is None:
            raise NotFound("会话不存在")
        members = await repo.list_session_members(conn, session_id)
        character_ids = [m["member_id"] for m in members if m["member_kind"] == "character"]
        characters = await repo.get_characters_by_ids(conn, character_ids)
        if not characters:
            raise NotFound("会话里没有角色")
        target = characters[0]
        if responder:
            target = next((c for c in characters if c["slug"] == responder), None)
            if target is None:
                raise NotFound(f"角色 {responder} 不在会话中")

        anchor = (
            await repo.get_anchor(conn, session["pinned_anchor_id"])
            if session.get("pinned_anchor_id")
            else await repo.get_current_anchor(conn, session["user_id"], session["work_id"])
        )
        if anchor is None:
            raise NotFound("作品没有可用的时间锚点")
        states = await repo.get_character_states(conn, anchor["id"], character_ids)
        persona = await repo.get_persona(conn, session["persona_id"])
        user_relation = await repo.get_user_character_relation(
            conn, session["persona_id"], target["id"]
        )
        peer_relations = await repo.list_effective_relationships(
            conn,
            session["persona_id"],
            session["work_id"],
            from_ids=[target["id"]],
            to_ids=character_ids,
        )
        declared_relations = await repo.list_declared_relations(
            conn, session["persona_id"], character_ids, anchor_seq=anchor["seq"]
        )
        from app.prompt import PromptContext, build_system_prompt

        ctx = PromptContext(
            character=target,
            anchor=anchor,
            state=states.get(target["id"]),
            session=session,
            persona=persona,
            user_relation=user_relation,
            peers=characters,
            peer_relations=peer_relations,
            declared_relations=declared_relations,
        )
        return {
            "session_id": session_id,
            "responder": target["slug"],
            "anchor": {"seq": anchor["seq"], "chapter_label": anchor["chapter_label"]},
            "system_prompt": build_system_prompt(ctx),
        }


@app.post("/api/sessions/{session_id}/messages", response_model=ChatResponse)
async def send_message(session_id: int, payload: SendMessageRequest) -> Dict[str, Any]:
    async with db.pool().acquire() as conn:
        return await chat_service.chat_once(
            conn,
            session_id,
            payload.content,
            _provider(),
            responder_slug=payload.responder_slug,
            message_kind=payload.kind,
        )


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _evolve_relations_later(
    session: Dict[str, Any],
    responder: Dict[str, Any],
    anchor: Dict[str, Any],
    user_text: str,
    reply_text: str,
    message_id: int,
) -> None:
    """流式回复结束后再跑关系演化，自己拿一个连接（失败不影响对话）。"""

    try:
        async with db.pool().acquire() as conn:
            await evolve_relations(
                conn,
                session=session,
                responder=responder,
                anchor=anchor,
                user_text=user_text,
                reply_text=reply_text,
                message_id=message_id,
            )
    except Exception:  # noqa: BLE001 - 演化是增值步骤
        pass


@app.post("/api/sessions/{session_id}/messages/stream")
async def send_message_stream(
    session_id: int, payload: SendMessageRequest, background: BackgroundTasks
) -> StreamingResponse:
    """流式回复。文本走 SSE，语音在第 3 期接入时挂在同一套事件上。"""

    if not (payload.content or "").strip():
        raise DomainError("消息内容不能为空")

    provider = _provider()

    async def event_stream() -> AsyncIterator[str]:
        try:
            async with db.pool().acquire() as conn:
                prepared = await chat_service.prepare_turn(
                    conn,
                    session_id,
                    payload.content,
                    responder_slug=payload.responder_slug,
                    message_kind=payload.kind,
                )
            yield _sse(
                "meta",
                {
                    "session_id": session_id,
                    "responder": {
                        "slug": prepared.responder["slug"],
                        "name": prepared.responder["name"],
                    },
                    "anchor": {
                        "seq": prepared.anchor["seq"],
                        "chapter_label": prepared.anchor["chapter_label"],
                        "name": prepared.anchor["name"],
                    },
                    "provider": provider.name,
                },
            )
            chunks: List[str] = []
            async for chunk in chat_service.stream_reply(prepared, provider):
                chunks.append(chunk)
                yield _sse("delta", {"text": chunk})

            text = "".join(chunks).strip() or "……"
            async with db.pool().acquire() as conn:
                reply = await chat_service.persist_reply(conn, prepared, text, provider)
            yield _sse(
                "done",
                {
                    "message_id": reply["id"],
                    "seq": reply["seq"],
                    "content": reply["content"],
                },
            )
            # 关系演化放在回复之后跑：真实模型要读一轮对话，不能让出字等它
            background.add_task(
                _evolve_relations_later,
                prepared.session,
                prepared.responder,
                prepared.anchor,
                payload.content,
                text,
                reply["id"],
            )
        except DomainError as exc:
            yield _sse("error", {"code": exc.code, "message": exc.message})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
