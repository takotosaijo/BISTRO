"""F19 缩水版：试验台页面能被服务端出来，而且它写死的东西都还存在。

页面是给人和眼睛用的，这里只钉两件机器能判定的事：
  1. GET / 确实返回那份 HTML（别哪天删了文件只剩 404）
  2. 页面里写死的作品 slug 在库里真实存在（改了 seed 的 slug 会让它静默变砖）
"""

from __future__ import annotations

from httpx import AsyncClient


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
