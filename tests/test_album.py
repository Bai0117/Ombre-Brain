"""相册：后台上传、我能把照片本身拿回来看、写说明、找、删。"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from errors import ToolInputError
from tools import album

# 最小的合法文件头就够了：相册只认文件头，不解码图片。
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "logger", MagicMock())
    return tmp_path


def test_add_update_delete(vault):
    p = album.add_photo(JPEG, caption="红豆粥", taken="2026-10-17")
    assert p["mime"] == "image/jpeg" and (vault / "_album" / p["file"]).read_bytes() == JPEG
    album.update_photo(p["id"], caption="等了一个半小时的红豆粥")
    assert album.load_photos()[0]["caption"].startswith("等了")
    assert album.read_photo(p["id"]) == (JPEG, "image/jpeg")
    assert album.delete_photo(p["id"])
    assert album.load_photos() == [] and not (vault / "_album" / p["file"]).exists()


def test_rejects_non_images_and_bad_dates(vault):
    with pytest.raises(ValueError):
        album.add_photo(b"<svg></svg>")
    with pytest.raises(ValueError):
        album.add_photo(b"")
    with pytest.raises(ValueError):
        album.add_photo(JPEG, taken="十月")
    with pytest.raises(ValueError):
        album.add_photo(b"\xff\xd8\xff" + b"x" * (album.core._MAX_BYTES + 1))
    assert album.load_photos() == []


@pytest.mark.asyncio
async def test_tool_lists_searches_and_returns_the_image(vault):
    a = album.add_photo(JPEG, caption="猫猫月月图标草稿", taken="2026-10-22")
    album.add_photo(PNG, caption="", taken="2026-10-17")

    text, data, _ = await album.dispatch()
    assert "相册 2 张" in text and f"[photo_id:{a['id']}] 2026-10-22 猫猫月月图标草稿" in text
    assert "（还没有说明）" in text and data is None

    text, _, _ = await album.dispatch(query="图标")
    assert a["id"] in text and "1 张" in text

    text, data, mime = await album.dispatch(photo_id=a["id"])
    assert data == JPEG and mime == "image/jpeg" and "猫猫月月" in text


@pytest.mark.asyncio
async def test_looking_at_an_uncaptioned_photo_asks_me_to_describe_it(vault):
    p = album.add_photo(PNG)
    text, data, _ = await album.dispatch(photo_id=p["id"])
    assert data == PNG and "还没有说明" in text
    text, data, _ = await album.dispatch(photo_id=p["id"], caption="她画的颠茄")
    assert data is None and "她画的颠茄" in text
    with pytest.raises(ToolInputError):
        await album.dispatch(photo_id="p_nope")
    with pytest.raises(ToolInputError):
        await album.dispatch(photo_id=p["id"], taken="下周")


@pytest.mark.asyncio
async def test_empty_album_says_where_photos_come_from(vault):
    text, data, _ = await album.dispatch()
    assert "后台「相册」" in text and data is None


@pytest.mark.asyncio
async def test_mcp_tool_returns_image_content(vault):
    import server

    p = album.add_photo(JPEG, caption="一张")
    tool = server.mcp._tool_manager.get_tool("album")
    result = await server.mcp.call_tool("album", {"photo_id": p["id"]})
    blocks = result[0] if isinstance(result, tuple) else result
    kinds = [b.type for b in blocks]
    assert kinds == ["text", "image"]
    assert blocks[1].mimeType == "image/jpeg"
    assert base64.b64decode(blocks[1].data) == JPEG
    # 只列表时是纯文字
    blocks = await server.mcp.call_tool("album", {})
    blocks = blocks[0] if isinstance(blocks, tuple) else blocks
    assert [b.type for b in blocks] == ["text"]
    assert tool is not None


class _FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(handler):
            for method in methods:
                self.routes[(method, path)] = handler
            return handler

        return decorator


class _Req:
    def __init__(self, method="GET", body=None, path_params=None):
        self.method = method
        self._body = body or {}
        self.path_params = path_params or {}
        self.headers = {}
        self.query_params = {}

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_dashboard_api(vault, monkeypatch):
    import web.album as album_web

    monkeypatch.setattr(album_web.sh, "_require_auth", lambda _r: None)
    mcp = _FakeMCP()
    album_web.register(mcp)
    root = mcp.routes[("POST", "/api/album")]
    item = mcp.routes[("POST", "/api/album/{photo_id}")]
    image = mcp.routes[("GET", "/api/album/{photo_id}/image")]

    data_url = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
    res = await root(_Req("POST", {"data": data_url, "caption": "小披萨", "taken": "2026-10-22", "w": 1600, "h": 1200}))
    photo = json.loads(res.body)["photo"]
    assert photo["w"] == 1600

    assert (await root(_Req("POST", {"data": "不是base64"}))).status_code == 400
    bad = base64.b64encode(b"GIF89a....").decode()
    assert (await root(_Req("POST", {"data": bad}))).status_code == 400

    listing = json.loads((await root(_Req("GET"))).body)
    assert listing["photos"][0]["caption"] == "小披萨"

    edit = await item(_Req("POST", {"caption": "小披萨游戏"}, {"photo_id": photo["id"]}))
    assert json.loads(edit.body)["photo"]["caption"] == "小披萨游戏"
    assert (await item(_Req("POST", {"taken": "x"}, {"photo_id": photo["id"]}))).status_code == 400

    img = await image(_Req("GET", path_params={"photo_id": photo["id"]}))
    assert img.body == JPEG and img.media_type == "image/jpeg"

    gone = await item(_Req("DELETE", path_params={"photo_id": photo["id"]}))
    assert json.loads(gone.body)["ok"]
    assert (await image(_Req("GET", path_params={"photo_id": photo["id"]}))).status_code == 404
