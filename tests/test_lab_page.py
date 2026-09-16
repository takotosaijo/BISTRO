"""F19 缩水版：试验台页面能被服务端出来，而且它写死的东西都还存在。

页面是给人和眼睛用的，这里只钉两件机器能判定的事：
  1. GET / 确实返回那份 HTML（别哪天删了文件只剩 404）
  2. 页面里写死的作品 slug 在库里真实存在（改了 seed 的 slug 会让它静默变砖）

另外顺带钉住 F24 的两个开发接口与身份切换器——试验台切不了身份，就等于没做。
"""

from __future__ import annotations

from httpx import AsyncClient

import uuid

WORK = "shuihu-100"


async def test_lab_page_is_served(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")

    body = response.text
    # 页面必须真的连着后端：锚点、角色、SSE、prompt 预览
    for marker in ("试验台", "时间线", "/api/works/", "messages/stream", "prompt-preview"):
        assert marker in body, f"试验台页面里少了「{marker}」"


async def test_lab_page_work_slug_exists(client: AsyncClient) -> None:
    body = (await client.get("/")).text
    works = (await client.get("/api/works")).json()
    slugs = {w["slug"] for w in works}
    assert any(f'"{slug}"' in body for slug in slugs), "页面里写死的作品 slug 不在库里"


async def test_lab_page_has_identity_switcher(client: AsyncClient) -> None:
    """F24 + 一人多身份：试验台要能建/切自己的角色，否则人设变体只能靠手输。"""

    body = (await client.get("/")).text
    for marker in ("我的角色", "personaList", "newPersona", "/personas", "/api/personas/"):
        assert marker in body, f"身份切换器少了「{marker}」"


async def test_one_account_can_hold_several_personas(client: AsyncClient) -> None:
    """一个账号下可以有多个身份，且互不干扰——这是 B 方案的核心。"""

    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"multi-{uuid.uuid4().hex[:8]}", "display_name": "多身份账号"},
        )
    ).json()
    first = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={"work_slug": WORK, "name": "玉娆", "identity": "林冲失散多年的私生女"},
        )
    ).json()
    second = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={"work_slug": WORK, "name": "苏娘", "identity": "林冲在东京时的旧相识"},
        )
    ).json()
    assert first["id"] != second["id"]

    listed = (await client.get(f"/api/users/{user['id']}/personas")).json()
    assert {p["name"] for p in listed} == {"玉娆", "苏娘"}

    # 两个身份各自开会话，互不串
    session_a = (
        await client.post(
            "/api/sessions",
            json={"persona_id": first["id"], "work_slug": WORK, "session_type": "direct",
                  "character_slugs": ["lin-chong"]},
        )
    ).json()
    session_b = (
        await client.post(
            "/api/sessions",
            json={"persona_id": second["id"], "work_slug": WORK, "session_type": "direct",
                  "character_slugs": ["lin-chong"]},
        )
    ).json()
    listed_a = (await client.get("/api/sessions", params={"persona_id": first["id"]})).json()
    listed_b = (await client.get("/api/sessions", params={"persona_id": second["id"]})).json()
    assert [s["id"] for s in listed_a] == [session_a["id"]]
    assert [s["id"] for s in listed_b] == [session_b["id"]]


async def test_persona_can_be_read_and_updated(client: AsyncClient) -> None:
    """切换身份后要能把人设读回来，改名后列表也跟着变（两个面板合并的前提）。"""

    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"persona-{uuid.uuid4().hex[:8]}", "display_name": "读回测试"},
        )
    ).json()
    created = (
        await client.post(
            f"/api/users/{user['id']}/personas",
            json={"work_slug": WORK, "name": "玉娆", "identity": "林冲失散多年的私生女，随母姓"},
        )
    ).json()
    got = (await client.get(f"/api/personas/{created['id']}")).json()
    assert got["name"] == "玉娆"
    assert "私生女" in got["identity"]

    updated = (
        await client.put(
            f"/api/personas/{created['id']}",
            json={"name": "玉娆（认亲后）", "identity": "林冲认下的女儿"},
        )
    ).json()
    assert updated["name"] == "玉娆（认亲后）"
    listed = (await client.get(f"/api/users/{user['id']}/personas")).json()
    assert listed[0]["name"] == "玉娆（认亲后）"
