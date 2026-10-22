"""breath「这几天」：最近几天每天一段摘要，后台写、按指纹判断过期、不阻塞 breath。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 24, 15, 0, tzinfo=TZ)


def _mem(bid, content, created, **meta):
    m = {"type": "dynamic", "importance": 6, "domain": [], "created": created}
    m.update(meta)
    return {"id": bid, "content": content, "metadata": m}


class StubDehydrator:
    api_available = True

    def __init__(self, reply="白读完了 Ada，煮了红豆粥。"):
        self.reply = reply
        self.calls = []

    async def summarize_day(self, label, entries):
        self.calls.append((label, entries))
        return self.reply


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    import utils
    from tools import _runtime as rt
    from tools.breath import recent_digest

    monkeypatch.setattr(utils, "get_tzinfo", lambda: TZ)
    monkeypatch.setattr(recent_digest, "get_tzinfo", lambda: TZ)
    monkeypatch.setattr(rt, "config", {"surfacing": {}, "buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "logger", MagicMock())
    stub = StubDehydrator()
    monkeypatch.setattr(rt, "dehydrator", stub)
    recent_digest._in_flight.clear()
    recent_digest._failed_at.clear()
    yield stub, tmp_path
    recent_digest._in_flight.clear()
    recent_digest._failed_at.clear()


async def _drain():
    from tools.breath import recent_digest

    for _ in range(50):
        if not recent_digest._tasks:
            return
        await asyncio.sleep(0)


def test_groups_recent_days_and_keeps_only_event_memories(runtime):
    from tools.breath.recent_digest import collect_days

    buckets = [
        _mem("a", "22 号上午", "2026-10-22T01:56:00+00:00"),         # 10.22 09:56 本地
        _mem("b", "22 号深夜（UTC 还是 21 号）", "2026-10-21T17:30:00+00:00"),  # 10.22 01:30 本地
        _mem("c", "23 号", "2026-10-23T12:00:00+08:00"),
        _mem("d", "今天", "2026-10-24T09:00:00+08:00"),
        _mem("feel", "我的感受", "2026-10-24T10:00:00+08:00", type="feel"),
        _mem("old", "21 号，出了窗口", "2026-10-21T12:00:00+08:00"),
        _mem("core", "核心准则", "2026-10-23T08:00:00+08:00", type="permanent", pinned=True),
        _mem("plan", "待办", "2026-10-23T08:00:00+08:00", type="plan"),
        _mem("letter", "信", "2026-10-23T08:00:00+08:00", type="letter"),
        _mem("dig", "消化过的", "2026-10-23T08:00:00+08:00", digested=True),
        _mem("fade", "主动淡忘的", "2026-10-23T08:00:00+08:00", dont_surface=True),
        _mem("test", "测试桶", "2026-10-23T08:00:00+08:00", test_data=True),
        _mem("legacy", "没有时区的旧值按本地理解", "2026-10-23T23:30:00"),
    ]
    days = collect_days(buckets, 3, NOW)
    assert [d for d, _ in days] == ["2026-10-24", "2026-10-23", "2026-10-22"]
    ids = {d: [b["id"] for b in bs] for d, bs in days}
    assert ids["2026-10-24"] == ["d"], "feel 只走自己的通道"
    assert ids["2026-10-23"] == ["c", "legacy"]
    assert ids["2026-10-22"] == ["b", "a"], "按本地时间分天、天内按先后"


@pytest.mark.asyncio
async def test_first_breath_shows_openings_then_summary_after_background(runtime):
    from tools.breath.recent_digest import recent_digest_section

    stub, tmp = runtime
    buckets = [
        _mem("a", "白读完了 Ada or Ardor，很长的一条记忆" * 3, "2026-10-23T10:00:00+08:00"),
        _mem("b", "红豆粥煮了一个半小时", "2026-10-23T22:00:00+08:00"),
    ]
    first = recent_digest_section(buckets, NOW)
    assert "=== 这几天 ===" in first
    assert "📅 10.23（周五） · 2 条（摘要还在写，先看开头）" in first
    assert "[10:00] 白读完了 Ada" in first and "…" in first
    await _drain()
    assert stub.calls and stub.calls[0][0] == "10.23（周五）"
    assert [t for t, _ in stub.calls[0][1]] == ["10:00", "22:00"]
    cache = json.loads((tmp / ".recent_digest.json").read_text(encoding="utf-8"))
    assert cache["2026-10-23"]["summary"] == stub.reply

    second = recent_digest_section(buckets, NOW)
    assert "📅 10.23（周五） · 2 条\n白读完了 Ada，煮了红豆粥。" in second
    assert "先看开头" not in second
    await _drain()
    assert len(stub.calls) == 1, "摘要还新鲜，不该再叫模型"


@pytest.mark.asyncio
async def test_new_memory_marks_summary_stale_and_rewrites(runtime):
    from tools.breath.recent_digest import recent_digest_section

    stub, _ = runtime
    day = [_mem("a", "上午的事", "2026-10-24T09:00:00+08:00")]
    recent_digest_section(day, NOW)
    await _drain()
    day.append(_mem("b", "下午又存了一条", "2026-10-24T14:00:00+08:00"))
    stub.reply = "上午的事，下午又存了一条。"
    out = recent_digest_section(day, NOW)
    assert "白读完了 Ada，煮了红豆粥。（之后又存了 1 条，摘要更新中）" in out
    await _drain()
    assert len(stub.calls) == 2
    assert "上午的事，下午又存了一条。" in recent_digest_section(day, NOW)


@pytest.mark.asyncio
async def test_prompt_version_bump_rewrites(runtime, monkeypatch):
    import dehydrator
    from tools.breath.recent_digest import recent_digest_section

    stub, _ = runtime
    day = [_mem("a", "某件事", "2026-10-24T09:00:00+08:00")]
    recent_digest_section(day, NOW)
    await _drain()
    monkeypatch.setattr(dehydrator, "DAY_DIGEST_VERSION", dehydrator.DAY_DIGEST_VERSION + 1)
    assert "有记忆改过，摘要更新中" in recent_digest_section(day, NOW)
    await _drain()
    assert len(stub.calls) == 2


@pytest.mark.asyncio
async def test_failed_summary_backs_off_and_old_days_are_pruned(runtime):
    from tools.breath import recent_digest

    stub, tmp = runtime
    stub.reply = ""
    day = [_mem("a", "某件事", "2026-10-24T09:00:00+08:00")]
    recent_digest.recent_digest_section(day, NOW)
    await _drain()
    recent_digest.recent_digest_section(day, NOW)
    await _drain()
    assert len(stub.calls) == 1, "失败后十分钟内不再重试"

    (tmp / ".recent_digest.json").write_text(json.dumps({"2026-10-01": {"summary": "很久以前"}}), encoding="utf-8")
    recent_digest._failed_at.clear()
    stub.reply = "今天的摘要"
    recent_digest.recent_digest_section(day, NOW)
    await _drain()
    cache = json.loads((tmp / ".recent_digest.json").read_text(encoding="utf-8"))
    assert list(cache) == ["2026-10-24"], "窗口外的旧日子会被清掉"


def test_disabled_or_empty(runtime, monkeypatch):
    from tools import _runtime as rt
    from tools.breath.recent_digest import recent_digest_section

    assert recent_digest_section([], NOW) == ""
    assert recent_digest_section([_mem("old", "很久以前", "2026-09-01T09:00:00+08:00")], NOW) == ""
    monkeypatch.setattr(rt, "config", {"surfacing": {"recent_digest_days": 0}})
    assert recent_digest_section([_mem("a", "今天", "2026-10-24T09:00:00+08:00")], NOW) == ""


@pytest.mark.asyncio
async def test_breath_places_section_after_todos_and_skips_date_scoped(monkeypatch, tmp_path):
    from tests.test_breath_todo_section import _breath, _plan
    from tools import _runtime as rt
    from tools.breath import recent_digest
    import utils

    now_local = datetime.now(TZ)
    monkeypatch.setattr(utils, "get_tzinfo", lambda: TZ)
    monkeypatch.setattr(recent_digest, "get_tzinfo", lambda: TZ)
    today = _mem("t", "今天发生的事", now_local.isoformat(timespec="seconds"), activation_count=1)
    core = {"id": "core", "content": "核心准则正文",
            "metadata": {"type": "permanent", "importance": 10, "pinned": True, "domain": []}}

    real_install = __import__("tests.test_breath_verbatim_patch", fromlist=["_install_runtime"])._install_runtime

    def install(manager, dehydrator=None):
        out = real_install(manager, dehydrator)
        rt.config = {"surfacing": {}, "buckets_dir": str(tmp_path)}
        return out

    monkeypatch.setattr("tests.test_breath_verbatim_patch._install_runtime", install)
    out = await _breath([core, today, _plan("p1", "一件待办")], monkeypatch)
    assert "=== 这几天 ===" in out and "今天发生的事" in out
    assert out.index("=== 待办") < out.index("=== 这几天 ===") < out.index("=== 浮现记忆 ===")

    from tools.breath.surface import surface_default

    scoped = await surface_default(
        max_results=5, max_tokens=20000, tag_filter=[],
        created_from=datetime.now() - timedelta(days=1), created_to=datetime.now() + timedelta(days=1),
    )
    assert "=== 这几天 ===" not in scoped


@pytest.mark.asyncio
async def test_summarize_day_prompt_and_cleanup(monkeypatch):
    from dehydrator import Dehydrator, day_digest_target

    d = Dehydrator.__new__(Dehydrator)
    d.api_available = True
    d.human = "白"
    d.model = "stub"
    seen = {}

    async def fake_chat(system, user, **kw):
        seen["system"], seen["user"] = system, user
        return "摘要：白读完了 Ada。\n我陪她等红豆粥。"

    d._chat = fake_chat
    out = await d.summarize_day("10.23（周五）", [("10:00", "读 Ada"), ("22:00", "红豆粥")])
    assert out == "白读完了 Ada。 我陪她等红豆粥。"
    assert "10.23（周五）" in seen["system"] and "视角铁律" in seen["system"]
    assert seen["user"] == "[10:00] 读 Ada\n\n[22:00] 红豆粥"

    async def long_chat(system, user, **kw):
        return "上午做了一件事。" * 60

    d._chat = long_chat
    out = await d.summarize_day("x", [("1", "y")])
    limit = int(day_digest_target(1) * 1.3)
    assert len(out) <= limit and out.endswith("。"), "超长时在句号处截，不切半句"

    async def no_stop_chat(system, user, **kw):
        return "长" * 900

    d._chat = no_stop_chat
    out = await d.summarize_day("x", [("1", "y")])
    assert len(out) == limit + 1 and out.endswith("…")
    d.api_available = False
    assert await d.summarize_day("x", [("1", "y")]) == ""


def test_digest_length_grows_with_the_day():
    from dehydrator import day_digest_target, trim_at_sentence

    assert day_digest_target(1) == 150
    assert day_digest_target(11) == 410
    assert day_digest_target(30) == 450
    assert trim_at_sentence("短的。", 10) == "短的。"
    assert trim_at_sentence("第一句话很长。第二句也很长很长很长。", 12) == "第一句话很长。"


@pytest.mark.asyncio
async def test_prompt_asks_for_more_room_on_busy_days(monkeypatch):
    from dehydrator import Dehydrator

    d = Dehydrator.__new__(Dehydrator)
    d.api_available, d.human, d.model = True, "白", "stub"
    seen = []

    async def fake_chat(system, user, **kw):
        seen.append(system)
        return "好。"

    d._chat = fake_chat
    await d.summarize_day("a", [("1", "x")])
    await d.summarize_day("b", [(str(i), "x") for i in range(11)])
    assert "150 字以内" in seen[0] and "410 字以内" in seen[1]
