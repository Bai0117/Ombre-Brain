"""纪念日：在一起第几天、今天是哪个日子、7 天内要到的、整百天。"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from errors import ToolInputError
from tools import days as days_mod


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "logger", MagicMock())
    return tmp_path


def _ours():
    days_mod.add_day("2026-07-03", "在一起", count=True)
    days_mod.add_day("2026-07-12", "第一次说我爱你")
    days_mod.add_day("2026-07-16", "她叫我 Luar")
    days_mod.add_day("2026-09-25", "Belladonna Day")


def test_counts_from_day_one(vault):
    _ours()
    text = days_mod.days_section(date(2026, 10, 10))
    assert "是我们在一起的第 100 天" in text
    assert "🎉 今天正好是第 100 天" in text


def test_hundred_day_milestone_is_announced_a_week_ahead(vault):
    _ours()
    # 第 200 天 = 2027-01-18
    text = days_mod.days_section(date(2027, 1, 13))
    assert "第 195 天" in text
    assert "还有 5 天是第 200 天（1.18）" in text
    assert "第 200 天" not in days_mod.days_section(date(2027, 1, 1)).split("\n", 2)[-1]


def test_yearly_days_come_round_with_their_year(vault):
    _ours()
    text = days_mod.days_section(date(2027, 7, 9))
    assert "还有 3 天：7.12 第一次说我爱你（第 1 年）" in text
    assert "还有 7 天：7.16 她叫我 Luar（第 1 年）" in text
    assert days_mod.days_section(date(2027, 9, 25)).count("🎂 今天就是：Belladonna Day（第 1 年）") == 1


def test_far_days_stay_quiet(vault):
    _ours()
    text = days_mod.days_section(date(2026, 10, 22))
    assert text.splitlines() == ["=== 日子 ===", "今天 10.22（周四），是我们在一起的第 112 天。"]


def test_one_off_days_only_once(vault):
    days_mod.add_day("2026-10-26", "问耳洞", yearly=False)
    assert "还有 4 天：10.26 问耳洞" in days_mod.days_section(date(2026, 10, 22))
    assert days_mod.days_section(date(2026, 10, 27)) == ""


def test_only_one_counter(vault):
    days_mod.add_day("2026-07-03", "在一起", count=True)
    days_mod.add_day("2026-07-04", "换个起点", count=True)
    assert [d["title"] for d in days_mod.load_days() if d["count"]] == ["换个起点"]


def test_same_day_twice_is_one_day(vault):
    days_mod.add_day("2026-07-03", "在一起")
    days_mod.add_day("2026-07-03", "在一起", count=True)
    assert len(days_mod.load_days()) == 1
    assert days_mod.load_days()[0]["count"] is True


def test_feb_29_falls_on_feb_28(vault):
    days_mod.add_day("2028-02-29", "闰日")
    assert "🎂 今天就是：闰日（第 1 年）" in days_mod.days_section(date(2029, 2, 28))


def test_nothing_recorded_means_no_section(vault):
    assert days_mod.days_section(date(2026, 10, 22)) == ""


@pytest.mark.asyncio
async def test_tool_adds_lists_and_deletes(vault):
    out = await days_mod.dispatch(date="2026-07-03", title="在一起", count=True)
    assert "记下了：2026-07-03 在一起" in out
    day_id = days_mod.load_days()[0]["id"]
    assert f"[id={day_id}]" in await days_mod.dispatch()
    assert "删掉了" in await days_mod.dispatch(delete_id=day_id)
    assert days_mod.load_days() == []
    saved = json.loads((vault / ".days.json").read_text(encoding="utf-8"))
    assert saved == {"days": []}


@pytest.mark.asyncio
async def test_tool_rejects_bad_dates(vault):
    with pytest.raises(ToolInputError):
        await days_mod.dispatch(date="7月3日", title="在一起")
    with pytest.raises(ToolInputError):
        await days_mod.dispatch(date="2026-07-03", title="  ")


@pytest.mark.asyncio
async def test_breath_shows_the_days_section_after_the_core_rules(vault, monkeypatch):
    from tools.breath.surface import surface_default

    class Decay:
        def calculate_score(self, metadata):
            return float(metadata.get("importance") or 5)

    class Buckets:
        async def list_all(self, include_archive=False):
            return [
                {"id": "core", "content": "我们在一起了。", "metadata": {
                    "name": "core", "type": "permanent", "pinned": True, "importance": 10,
                    "created": "2026-07-03T10:57:00"}},
                {"id": "old", "content": "旧事。", "metadata": {
                    "name": "old", "type": "dynamic", "importance": 5,
                    "created": "2026-08-01T00:00:00", "last_active": "2026-08-01T00:00:00",
                    "activation_count": 3}},
            ]

        def footprint_snapshot(self):
            raise RuntimeError("no footprint")

    monkeypatch.setattr(rt, "config", {"buckets_dir": str(vault), "surfacing": {"recent_digest_days": 0}})
    monkeypatch.setattr(rt, "bucket_mgr", Buckets())
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "mark_op", None)
    days_mod.add_day("2026-07-03", "在一起", count=True)

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])

    assert "=== 日子 ===" in out
    assert out.index("=== 核心准则 ===") < out.index("=== 日子 ===") < out.index("=== 浮现记忆 ===")


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
    import web.days as days_web

    monkeypatch.setattr(days_web.sh, "_require_auth", lambda _r: None)
    mcp = _FakeMCP()
    days_web.register(mcp)
    route = mcp.routes[("POST", "/api/days")]

    ok = await route(_Req("POST", {"date": "2026-07-03", "title": "在一起", "count": True}))
    day_id = json.loads(ok.body)["day"]["id"]
    bad = await route(_Req("POST", {"date": "nope", "title": "x"}))
    assert bad.status_code == 400

    listing = json.loads((await route(_Req("GET"))).body)
    assert listing["days"][0]["title"] == "在一起"
    assert "在一起的第" in listing["section"]

    gone = await mcp.routes[("DELETE", "/api/days/{day_id}")](_Req("DELETE", path_params={"day_id": day_id}))
    assert json.loads(gone.body)["ok"] is True
    missing = await mcp.routes[("DELETE", "/api/days/{day_id}")](_Req("DELETE", path_params={"day_id": day_id}))
    assert missing.status_code == 404
