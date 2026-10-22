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
    from dehydrator import Dehydrator

    d = Dehydrator.__new__(Dehydrator)
    d.api_available = True
    d.human = "白"
    d.model = "stub"
    d.api_format, d.base_url, d.extra_body = "openai_compat", "https://api.deepseek.com/v1", {}
    seen = {}

    async def fake_chat(system, user, **kw):
        seen["system"], seen["user"] = system, user
        return "这是两条的摘要：\n[2] [22:00] 我陪她等红豆粥\n[1] 白读完了 Ada，\n[9] 编号不存在的行要丢掉"

    d._chat = fake_chat
    out = await d.summarize_day("10.23（周五）", [("10:00", "读 Ada"), ("22:00", "红豆粥")])
    assert out == "白读完了 Ada。我陪她等红豆粥。", "按编号排好、去掉抄来的时间、每句收尾"
    assert "10.23（周五）" in seen["system"] and "视角铁律" in seen["system"]
    assert "输出正好 2 行" in seen["system"]
    assert seen["user"] == "[1] [10:00] 读 Ada\n\n[2] [22:00] 红豆粥"

    async def no_format(system, user, **kw):
        return "我没按格式写，一整段话。"

    d._chat = no_format
    assert await d.summarize_day("x", [("1", "y"), ("2", "z")]) == "", "没按格式就回退"
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
async def test_prompt_budget_per_line(monkeypatch):
    d = _bare()
    seen = []

    async def fake_chat(system, user, **kw):
        seen.append(system)
        return "[1] 好。"

    d._chat = fake_chat
    await d.summarize_day("a", [("1", "x")])
    await d.summarize_day("b", [(str(i), "x") for i in range(11)])
    assert "大约 150 字、最多 240 字" in seen[0]
    assert "输出正好 11 行" in seen[1] and "大约 37 字、最多 59 字" in seen[1]


@pytest.mark.asyncio
async def test_day_digest_leaves_room_for_thinking():
    """真机上 deepseek-flash 在 4096 以内连一天 1 条记忆都想不完（正文为空）。"""
    from dehydrator import Dehydrator

    d = Dehydrator.__new__(Dehydrator)
    d.api_available, d.human, d.model = True, "白", "stub"
    d.api_format, d.base_url, d.extra_body = "openai_compat", "https://api.deepseek.com/v1", {}
    budgets = []

    async def fake_chat(system, user, max_tokens=None, **kw):
        budgets.append(max_tokens)
        return "好。"

    d._chat = fake_chat
    d.digest_max_tokens = 8192
    await d.summarize_day("a", [("1", "x")])
    d.digest_max_tokens = 32768
    await d.summarize_day("a", [("1", "x")])
    assert budgets == [16384, 32768]



def _bare(base_url="https://api.deepseek.com/v1", extra=None, fmt="openai_compat"):
    from dehydrator import Dehydrator

    d = Dehydrator.__new__(Dehydrator)
    d.api_available, d.human, d.model = True, "白", "deepseek-flash"
    d.api_format, d.base_url, d.extra_body = fmt, base_url, dict(extra or {})
    return d


@pytest.mark.asyncio
async def test_summaries_turn_thinking_off_on_deepseek():
    d = _bare(extra={"foo": 1})
    seen = []

    async def fake_chat(system, user, **kw):
        seen.append(kw)
        return "好。"

    d._chat = fake_chat
    await d.summarize_day("a", [("1", "x")])
    await d.summarize_plan("给聊天后端加锁")
    for kw in seen:
        assert kw["extra_body"] == {"foo": 1, "thinking": {"type": "disabled"}}
        assert kw["timeout"] == 300.0
    assert d.extra_body == {"foo": 1}, "不改动配置里的 extra_body"


@pytest.mark.asyncio
async def test_other_providers_get_no_unknown_fields():
    d = _bare(base_url="https://generativelanguage.googleapis.com/v1beta/openai")
    seen = []

    async def fake_chat(system, user, **kw):
        seen.append(kw)
        return "好。"

    d._chat = fake_chat
    await d.summarize_day("a", [("1", "x")])
    assert "extra_body" not in seen[0]


@pytest.mark.asyncio
async def test_falls_back_when_model_rejects_the_switch():
    d = _bare()
    calls = []

    class BadRequest(Exception):
        status_code = 400

    async def fake_chat(system, user, **kw):
        calls.append(kw.get("extra_body"))
        if kw.get("extra_body"):
            raise BadRequest("unknown field: thinking")
        return "[1] 以前那样写出来的。"

    d._chat = fake_chat
    assert await d.summarize_day("a", [("1", "x")]) == "以前那样写出来的。"
    assert calls[0]["thinking"] == {"type": "disabled"} and calls[1] is None


@pytest.mark.asyncio
async def test_transient_errors_are_not_retried_twice():
    d = _bare()
    calls = []

    class Busy(Exception):
        status_code = 503

    async def fake_chat(system, user, **kw):
        calls.append(kw)
        raise Busy("overloaded")

    d._chat = fake_chat
    assert await d.summarize_day("a", [("1", "x")]) == ""
    assert len(calls) == 1, "瞬时错误交给 _chat 自己的退避重试，这里不再追加一轮"


@pytest.mark.asyncio
async def test_chat_once_sends_per_request_options():
    """真正发出去的请求里带上 thinking 开关和单次超时；不传时仍用配置的 extra_body。"""
    from types import SimpleNamespace

    d = _bare(extra={"cfg": True})
    d.temperature, d.max_tokens = 0.1, 1024
    sent = []

    async def create(**kw):
        sent.append(kw)
        msg = SimpleNamespace(content="好。")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    d.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    await d._chat_once("s", "u", extra_body={"thinking": {"type": "disabled"}}, timeout=300.0)
    await d._chat_once("s", "u")
    assert sent[0]["extra_body"] == {"thinking": {"type": "disabled"}} and sent[0]["timeout"] == 300.0
    assert sent[1]["extra_body"] == {"cfg": True} and "timeout" not in sent[1]



@pytest.mark.asyncio
async def test_every_memory_reaches_the_model_on_a_long_day():
    """以前总共 9000 字：长记忆多的日子，后面几条根本没送进去。现在每条均分。"""
    d = _bare()
    seen = []

    async def fake_chat(system, user, **kw):
        seen.append((system, user))
        return "\n".join(f"[{i}] 第 {i} 件事。" for i in range(1, 12))

    d._chat = fake_chat
    entries = [(f"{i:02d}:00", f"第 {i} 条记忆的开头。" + "很长的细节" * 400) for i in range(1, 12)]
    out = await d.summarize_day("10.22", entries)
    system, user = seen[0]
    for i in range(1, 12):
        assert f"[{i}] [{i:02d}:00] 第 {i} 条记忆的开头。" in user
    assert out.startswith("第 1 件事。") and out.endswith("第 11 件事。")
    assert "不要照抄" in system and "电报" in system


@pytest.mark.asyncio
async def test_long_lines_are_capped_but_every_line_kept():
    d = _bare()

    async def fake_chat(system, user, **kw):
        long_line = "凌晨搬家，" + "细节很多很多，" * 30
        return f"[1] {long_line}\n[2] 下午改了后台\n[3] 晚上她画好了猫猫月月"

    d._chat = fake_chat
    out = await d.summarize_day("10.22", [("1", "a"), ("2", "b"), ("3", "c")])
    first, rest = out.split("。", 1)
    assert len(first) <= 92, "单行被限在 line_cap 以内（在逗号处断）"
    assert rest == "下午改了后台。晚上她画好了猫猫月月。", "后面的事都在"