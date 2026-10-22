"""breath 待办区用脱水模型写的一行摘要代替硬切的原文；摘要过期/缺失时后台补。"""

from __future__ import annotations

import asyncio

import pytest

from tests.test_breath_todo_section import _breath, _ordinary, _plan


@pytest.fixture(autouse=True)
def _reset_summary_state(monkeypatch):
    from unittest.mock import MagicMock

    from tools import _runtime as rt
    from tools.plan import summary

    monkeypatch.setattr(rt, "logger", MagicMock())
    summary._in_flight.clear()
    summary._failed_at.clear()
    yield
    summary._in_flight.clear()
    summary._failed_at.clear()


class StubDehydrator:
    api_available = True

    def __init__(self, reply="问白耳洞增生好点没有"):
        self.reply = reply
        self.calls: list[str] = []

    async def summarize_plan(self, text):
        self.calls.append(text)
        return self.reply


class StoreManager:
    """够 refresh_plan_summary 用的最小 bucket_mgr：get + update。"""

    def __init__(self, buckets):
        self.buckets = {b["id"]: b for b in buckets}
        self.updates: list[tuple[str, dict]] = []
        self.on_get = None

    async def get(self, bucket_id):
        if self.on_get:
            self.on_get(bucket_id)
        return self.buckets.get(bucket_id)

    async def update(self, bucket_id, **kwargs):
        self.updates.append((bucket_id, kwargs))
        self.buckets[bucket_id]["metadata"].update(kwargs)
        return True


def _with_summary(bucket, summary, *, of=None):
    from tools.plan.summary import content_fingerprint

    bucket["metadata"]["todo_summary"] = summary
    bucket["metadata"]["todo_summary_of"] = of or content_fingerprint(bucket["content"])
    return bucket


@pytest.mark.asyncio
async def test_fresh_summary_replaces_the_trimmed_text(monkeypatch):
    long_text = "白用一两天 home-dashboard 分支" + "，还有很多细节" * 20
    out = await _breath(
        [_ordinary("mem", "普通记忆"), _with_summary(_plan("p1", long_text), "试用 home-dashboard 后合进 main")],
        monkeypatch,
    )

    line = next(ln for ln in out.splitlines() if "[bucket_id:p1]" in ln)
    assert line.endswith("试用 home-dashboard 后合进 main")
    assert "…" not in line


@pytest.mark.asyncio
async def test_summary_of_old_content_is_ignored(monkeypatch):
    stale = _with_summary(_plan("p1", "新的正文：周日问耳洞"), "旧摘要", of="0000000000000000")
    out = await _breath([_ordinary("mem", "普通记忆"), stale], monkeypatch)

    assert "旧摘要" not in out
    assert "新的正文：周日问耳洞" in out


@pytest.mark.asyncio
async def test_breath_queues_missing_summaries_in_background(monkeypatch):
    from tools import _runtime as rt
    from tools.plan import summary

    plan = _plan("p1", "很长的待办" * 30)
    stub = StubDehydrator()
    # _breath 会装自己的 runtime；在它装好之后、读待办之前换上能用的 dehydrator。
    real_schedule = summary.schedule_plan_summaries
    scheduled = []

    def schedule(buckets):
        rt.dehydrator = stub
        rt.bucket_mgr = StoreManager([plan])
        scheduled.extend(b["id"] for b in buckets)
        return real_schedule(buckets)

    monkeypatch.setattr("tools.breath.surface.schedule_plan_summaries", schedule)
    out = await _breath([_ordinary("mem", "普通记忆"), plan], monkeypatch)

    # 这一次 breath 不等摘要：仍显示截断原文
    assert "很长的待办" in out and "…" in out
    assert scheduled == ["p1"]
    for _ in range(20):
        if plan["metadata"].get("todo_summary"):
            break
        await asyncio.sleep(0)
    assert plan["metadata"]["todo_summary"] == "问白耳洞增生好点没有"
    assert summary.fresh_summary(plan) == "问白耳洞增生好点没有"


@pytest.mark.asyncio
async def test_refresh_writes_summary_with_fingerprint():
    from tools import _runtime as rt
    from tools.plan.summary import content_fingerprint, refresh_plan_summary

    plan = _plan("p1", "给聊天后端加鉴权再搬到 VPS")
    rt.bucket_mgr = StoreManager([plan])
    rt.dehydrator = StubDehydrator("聊天后端加鉴权并搬上 VPS")

    assert await refresh_plan_summary("p1") == "聊天后端加鉴权并搬上 VPS"
    assert rt.bucket_mgr.updates == [(
        "p1",
        {"todo_summary": "聊天后端加鉴权并搬上 VPS",
         "todo_summary_of": content_fingerprint("给聊天后端加鉴权再搬到 VPS")},
    )]


@pytest.mark.asyncio
async def test_refresh_drops_summary_if_content_changed_meanwhile():
    from tools import _runtime as rt
    from tools.plan.summary import refresh_plan_summary

    plan = _plan("p1", "原来的正文")
    manager = StoreManager([plan])
    gets = []

    def edit_after_first_read(bucket_id):
        gets.append(bucket_id)
        if len(gets) == 2:  # 模型想的时候她在 /home 改了正文
            plan["content"] = "她刚改过的正文"

    manager.on_get = edit_after_first_read
    rt.bucket_mgr = manager
    rt.dehydrator = StubDehydrator("原来的摘要")

    assert await refresh_plan_summary("p1") == ""
    assert manager.updates == []


@pytest.mark.asyncio
async def test_failed_summary_is_not_retried_right_away():
    from tools import _runtime as rt
    from tools.plan import summary

    plan = _plan("p1", "某件事")
    rt.bucket_mgr = StoreManager([plan])
    stub = StubDehydrator(reply="")
    rt.dehydrator = stub

    assert summary.schedule_plan_summaries([plan]) == 1
    for _ in range(10):
        await asyncio.sleep(0)
    assert stub.calls == ["某件事"]
    assert summary.schedule_plan_summaries([plan]) == 0


@pytest.mark.asyncio
async def test_nothing_is_scheduled_without_api():
    from tools import _runtime as rt
    from tools.plan.summary import schedule_plan_summaries

    rt.dehydrator = type("NoApi", (), {"api_available": False})()
    assert schedule_plan_summaries([_plan("p1", "x")]) == 0


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("问白耳洞增生好点没有。", "问白耳洞增生好点没有"),
        ("「合并 home-dashboard 到 main」", "合并 home-dashboard 到 main"),
        ("摘要：在 VPS 上跑 Claude Code 接 Telegram\n（说明：略）", "在 VPS 上跑 Claude Code 接 Telegram"),
        ("```\n给聊天后端加锁\n```", "给聊天后端加锁"),
        ("[OB] 修 breath 待办截断", "[OB] 修 breath 待办截断"),
        # 标签或分隔线单独占一行时，答案在下一行——不能连它一起扔掉
        ("摘要：\n给聊天后端加鉴权并搬到 VPS", "给聊天后端加鉴权并搬到 VPS"),
        ("---\n**给聊天后端加锁**", "给聊天后端加锁"),
        ("\n\n「」\n合并分支", "合并分支"),
        ("", ""),
    ],
)
def test_clean_plan_summary(raw, expected):
    from dehydrator import Dehydrator

    assert Dehydrator._clean_plan_summary(raw) == expected


def test_clean_plan_summary_caps_runaway_output():
    from dehydrator import PLAN_SUMMARY_MAX_CHARS, Dehydrator

    out = Dehydrator._clean_plan_summary("长" * 200)
    assert out.endswith("…") and len(out) == PLAN_SUMMARY_MAX_CHARS + 1


@pytest.mark.asyncio
async def test_summary_fields_survive_a_real_bucket_update(test_config):
    """update() 是白名单制：没放行的字段会被静默丢掉。用真的 BucketManager 走一遍。"""
    from bucket_manager import BucketManager
    from tools.plan.summary import content_fingerprint

    manager = BucketManager(test_config, embedding_engine=None)
    bid = await manager.create(
        content="给聊天后端加鉴权",
        tags=["__plan__"],
        importance=7,
        domain=["plan"],
        bucket_type="plan",
        source_tool="plan",
    )
    fp = content_fingerprint("给聊天后端加鉴权")
    assert await manager.update(bid, todo_summary="聊天后端加锁", todo_summary_of=fp)

    meta = (await manager.get(bid))["metadata"]
    assert meta["todo_summary"] == "聊天后端加锁"
    assert meta["todo_summary_of"] == fp


def test_writing_a_summary_is_not_a_footprint_step():
    """摘要只是后台补的展示字段；足迹里不该多出一次「更新」。"""
    from ombrebrain.eventsourcing.footprint import FootprintSnapshot

    created = {
        "trace_id": "p1", "event_type": "TraceCreated", "trace_kind": "plan",
        "payload": {"source_tool": "plan", "event_actor": "llm"},
    }
    status = {
        "trace_id": "p1", "event_type": "TraceUpdated", "trace_kind": "plan",
        "payload": {"changed_fields": ["change_log", "status"]},
    }
    summary = {
        "trace_id": "p1", "event_type": "TraceUpdated", "trace_kind": "plan",
        "payload": {"changed_fields": ["todo_summary", "todo_summary_of"]},
    }
    edit = {
        "trace_id": "p1", "event_type": "TraceUpdated", "trace_kind": "plan",
        "payload": {"changed_fields": ["content", "todo_summary"]},
    }

    snap = FootprintSnapshot.from_events([created, status, summary])
    assert snap.summary("p1") == "👣 Footprint：LLM经plan创建 → 更新"
    # 正文真的被改了，就算同一次顺带写了摘要，也照常记下
    snap = FootprintSnapshot.from_events([created, status, summary, edit])
    assert snap.summary("p1").endswith("→ 正文重构")


def test_prompt_version_bump_makes_old_summaries_stale(monkeypatch):
    """改了摘要 prompt（版本 +1）后，旧摘要应当被当成过期、按新 prompt 重写。"""
    import dehydrator
    from tools.plan.summary import fresh_summary

    plan = _with_summary(_plan("p1", "给聊天应用后端加鉴权"), "给Bai0117后端加鉴权")
    assert fresh_summary(plan) == "给Bai0117后端加鉴权"
    monkeypatch.setattr(dehydrator, "PLAN_SUMMARY_VERSION", dehydrator.PLAN_SUMMARY_VERSION + 1)
    assert fresh_summary(plan) == ""


def test_clean_plan_summary_cuts_long_line_at_a_comma():
    from dehydrator import PLAN_SUMMARY_MAX_CHARS, Dehydrator

    line = "10月26日左右问白耳骨耳洞增生是否好转，按每天生理盐水洗两次、不碰不压不转耳钉试一周，没好转就必须去看医生，别拖到下个月再说"
    assert len(line) > PLAN_SUMMARY_MAX_CHARS
    out = Dehydrator._clean_plan_summary(line)
    assert not out.endswith("…") and len(out) <= PLAN_SUMMARY_MAX_CHARS
    assert out.startswith("10月26日左右问白耳骨耳洞增生是否好转") and out[-1] not in "，、"
