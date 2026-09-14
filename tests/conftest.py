from __future__ import annotations

import os
import sys
import uuid
from typing import Any, AsyncIterator, Dict, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 测试环境一律使用离线 mock provider，并且必须在导入 app.config 之前设好
os.environ.setdefault("BISTRO_LLM_PROVIDER", "mock")
TEST_DSN = os.environ.get("BISTRO_TEST_DATABASE_URL")
if TEST_DSN:
    os.environ["BISTRO_DATABASE_URL"] = TEST_DSN

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

WORK = "shuihu-100"


@pytest.fixture(autouse=True)
def _require_database() -> None:
    if not TEST_DSN:
        pytest.skip("未设置 BISTRO_TEST_DATABASE_URL，跳过需要数据库的测试")


@pytest.fixture
async def app_env() -> AsyncIterator[None]:
    await db.connect()
    try:
        yield
    finally:
        await db.disconnect()


@pytest.fixture
async def conn(app_env: None):
    async with db.pool().acquire() as connection:
        yield connection


@pytest.fixture
async def client(app_env: None) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def make_user(
    client: AsyncClient,
    chapter_no: int,
    character_slugs: list,
    session_type: str = "direct",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """建一个用户、写好用户人设、拨好时间线、开一个会话。"""

    external_id = f"test-{uuid.uuid4().hex[:10]}"
    response = await client.post(
        "/api/users", json={"external_id": external_id, "display_name": "测试用户"}
    )
    assert response.status_code == 200, response.text
    user = response.json()

    response = await client.put(
        f"/api/users/{user['id']}/persona",
        json={
            "work_slug": WORK,
            "name": "张三",
            "identity": "东京城里开酒铺的掌柜",
            "speech_style": "客气里带点精明",
        },
    )
    assert response.status_code == 200, response.text

    response = await client.put(
        f"/api/users/{user['id']}/timeline",
        json={"work_slug": WORK, "chapter_no": chapter_no},
    )
    assert response.status_code == 200, response.text

    response = await client.post(
        "/api/sessions",
        json={
            "user_id": user["id"],
            "work_slug": WORK,
            "session_type": session_type,
            "character_slugs": character_slugs,
        },
    )
    assert response.status_code == 200, response.text
    return user, response.json()
