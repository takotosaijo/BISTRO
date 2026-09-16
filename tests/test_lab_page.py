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
    """F24：试验台要能切换「以谁的身份进入」，否则人设变体只能靠手输。"""

    body = (await client.get("/")).text
    for marker in ("以谁的身份进入", "devUser", "/api/dev/users", "/persona?work_slug="):
        assert marker in body, f"身份切换器少了「{marker}」"


async def test_dev_users_lists_identities_with_persona(client: AsyncClient) -> None:
    """开发接口给出可选账号，带人设摘要——试验台的下拉框靠它填。"""

    users = (await client.get("/api/dev/users", params={"limit": 5})).json()
    assert users, "开发库里至少应该有账号"
    assert {"id", "external_id", "persona_name", "persona_identity"} <= set(users[0])
    # 默认过滤掉测试跑出来的垃圾账号，否则下拉框会被 iso-* / test-* 淹掉
    assert not [u for u in users if u["external_id"].startswith(("test-", "iso-", "anchor-"))]

    everyone = (await client.get("/api/dev/users", params={"prefixes": "all", "limit": 100})).json()
    assert len(everyone) >= len(users)


async def test_persona_can_be_read_back(client: AsyncClient) -> None:
    """切换身份后要能把人设读回来填进表单（以前只有 PUT，没有 GET）。"""

    user = (
        await client.post(
            "/api/users",
            json={"external_id": f"persona-{uuid.uuid4().hex[:8]}", "display_name": "读回测试"},
        )
    ).json()
    await client.put(
        f"/api/users/{user['id']}/persona",
        json={"work_slug": WORK, "name": "玉娆", "identity": "林冲失散多年的私生女，随母姓"},
    )
    body = (await client.get(f"/api/users/{user['id']}/persona")).json()
    assert body["persona"]["name"] == "玉娆"
    assert "私生女" in body["persona"]["identity"]
