"""书架 / 片单：记、改、拿掉、按年查、口味。"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from errors import ToolInputError
from tools import shelf


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "logger", MagicMock())
    return tmp_path


def _fill():
    shelf.save_item(title="2666", creator="波拉尼奥", finished="2026-10-14", rating=5,
                    tags="拉美, 长篇, 暴力", note="两天看完")
    shelf.save_item(title="Ada or Ardor", creator="纳博科夫", finished="2026-10-17", rating=4,
                    tags=["纳博科夫", "长篇"])
    shelf.save_item(title="染血之室", creator="Angela Carter", finished="2026-10-21", rating=2,
                    tags="童话改写", note="像给小孩看的，只有精灵王咬住了")
    shelf.save_item(title="微暗的火", creator="纳博科夫", status="reading", tags="纳博科夫")
    shelf.save_item(title="Dogville", kind="film", creator="Lars von Trier", finished="2026-10-18",
                    rating=5, tags="对话多")
    shelf.save_item(title="Prisoners", kind="film", status="want", tags="侦探, 秋天")
    shelf.save_item(title="跳房子", creator="科塔萨尔", finished="2025-12", rating=4)


def test_same_kind_and_title_updates_instead_of_adding(vault):
    a, created = shelf.save_item(title="Ada or Ardor", finished="2026-10-17")
    assert created
    b, created = shelf.save_item(title="ada  or ardor", rating=4)
    assert not created and a["id"] == b["id"]
    assert len(shelf.load_items()) == 1
    assert shelf.load_items()[0]["finished"] == "2026-10-17"   # 没传的字段不动
    # 同名不同类（书 vs 电影）是两项
    shelf.save_item(title="Ada or Ardor", kind="film")
    assert len(shelf.load_items()) == 2


def test_update_by_id_keeps_kind(vault):
    item, _ = shelf.save_item(title="Dogville", kind="film")
    shelf.save_item(title="", item_id=item["id"], note="对他人的无要求也是一种傲慢")
    saved = shelf.load_items()[0]
    assert saved["kind"] == "film" and saved["note"].startswith("对他人")


def test_validation(vault):
    with pytest.raises(ValueError):
        shelf.save_item(title="x", kind="podcast")
    with pytest.raises(ValueError):
        shelf.save_item(title="x", rating=6)
    with pytest.raises(ValueError):
        shelf.save_item(title="x", finished="十月")
    with pytest.raises(ValueError):
        shelf.save_item(title="")
    assert shelf.load_items() == []


def test_taste(vault):
    _fill()
    t = shelf.taste(today=date(2026, 10, 22))
    assert t["this_year_total"] == 4
    assert t["this_year"] == {"book": 3, "film": 1, "series": 0, "game": 0}
    tags = {x["tag"]: x for x in t["top_tags"]}
    assert tags["长篇"]["count"] == 2 and tags["长篇"]["avg"] == 4.5
    creators = {x["creator"]: x for x in t["top_creators"]}
    assert creators["纳博科夫"]["count"] == 1   # 在看的不算看完
    assert [i["title"] for i in t["loved"]][:2] == ["Dogville", "Ada or Ardor"]
    assert [i["title"] for i in t["disliked"]] == ["染血之室"]
    assert [i["title"] for i in t["reading"]] == ["微暗的火"]
    assert [i["title"] for i in t["want"]] == ["Prisoners"]


@pytest.mark.asyncio
async def test_tool_overview_and_queries(vault):
    _fill()
    out = await shelf.dispatch()
    assert "年看完" in out and "在看：" in out and "《微暗的火》" in out
    assert "她不太喜欢的：《染血之室》" in out
    assert "想看：" in out

    by_year = await shelf.dispatch(year=2025)
    assert "《跳房子》" in by_year and "《2666》" not in by_year
    found = await shelf.dispatch(query="纳博科夫")
    assert "《Ada or Ardor》" in found and "《微暗的火》" in found


@pytest.mark.asyncio
async def test_tool_add_update_delete(vault):
    out = await shelf.dispatch(title="An Inspector Calls", kind="film", finished="2026-10-20", rating=3)
    assert out.startswith("放上书架了")
    item_id = shelf.load_items()[0]["id"]
    out = await shelf.dispatch(item_id=item_id, note="好简单")
    assert out.startswith("改好了") and "好简单" in out
    assert shelf.load_items()[0]["rating"] == 3   # rating=-1 不改
    await shelf.dispatch(delete_id=item_id)
    assert shelf.load_items() == []
    with pytest.raises(ToolInputError):
        await shelf.dispatch(delete_id=item_id)
    with pytest.raises(ToolInputError):
        await shelf.dispatch(title="x", kind="podcast")
    assert json.loads((vault / ".shelf.json").read_text(encoding="utf-8")) == {"items": []}


@pytest.mark.asyncio
async def test_empty_shelf_says_how_to_start(vault):
    assert "书架还是空的" in await shelf.dispatch()


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
    import web.shelf as shelf_web

    monkeypatch.setattr(shelf_web.sh, "_require_auth", lambda _r: None)
    mcp = _FakeMCP()
    shelf_web.register(mcp)
    route = mcp.routes[("POST", "/api/shelf")]

    res = await route(_Req("POST", {"title": "鬼入侵", "kind": "series", "finished": "2026-10-20",
                                     "rating": 4, "tags": "恐怖, 家族", "note": ""}))
    item = json.loads(res.body)["item"]
    assert item["kind"] == "series" and item["tags"] == ["恐怖", "家族"]
    edit = await route(_Req("POST", {"id": item["id"], "title": "鬼入侵", "kind": "series", "rating": 5}))
    assert json.loads(edit.body)["created"] is False
    bad = await route(_Req("POST", {"title": "x", "rating": 9}))
    assert bad.status_code == 400

    listing = json.loads((await route(_Req("GET"))).body)
    assert listing["items"][0]["rating"] == 5
    assert "taste" in listing and listing["kinds"]["series"] == "剧"

    delete = mcp.routes[("DELETE", "/api/shelf/{item_id}")]
    assert json.loads((await delete(_Req("DELETE", path_params={"item_id": item["id"]}))).body)["ok"]
    assert (await delete(_Req("DELETE", path_params={"item_id": item["id"]}))).status_code == 404
