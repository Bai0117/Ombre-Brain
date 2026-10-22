"""带日期的待办：plan(due=...)、breath 待办排序与标注、后台改到期日。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from errors import ToolInputError
from tools.plan.due import due_label, due_state, parse_due
from utils import get_tzinfo

TODAY = datetime.now(get_tzinfo()).date()


def test_parse_and_label():
    assert parse_due("") == ""
    assert parse_due("2026-10-26") == "2026-10-26"
    with pytest.raises(ValueError):
        parse_due("10.26")
    today = date(2026, 10, 22)
    assert due_state({"due": "2026-10-26"}, today) == 4
    assert due_label({"due": "2026-10-26"}, today) == "⏰ 还有 4 天（10.26）"
    assert due_label({"due": "2026-10-22"}, today) == "⏰ 今天（10.22）"
    assert due_label({"due": "2026-10-20"}, today) == "⏰ 过了 2 天（10.20）"
    assert due_label({"due": "2026-12-01"}, today) == "📅 12.1"
    assert due_label({}, today) == "" and due_state({"due": "坏的"}, today) is None


def _plan(bid, text, *, weight=0.5, due="", created="2026-10-22T01:56:00"):
    m = {"name": bid, "type": "plan", "status": "active", "weight": weight,
         "created": created, "todo_summary": text, "todo_summary_of": "x"}
    if due:
        m["due"] = due
    return {"id": bid, "content": text, "metadata": m}


def test_todo_section_puts_urgent_ones_first(monkeypatch):
    from tools.breath import surface

    monkeypatch.setattr(surface, "schedule_plan_summaries", lambda _p: None)
    monkeypatch.setattr(surface, "fresh_summary", lambda b: b["metadata"]["todo_summary"])
    monkeypatch.setattr(rt, "config", {})
    soon = (TODAY + timedelta(days=4)).isoformat()
    later = (TODAY + timedelta(days=40)).isoformat()
    late = (TODAY - timedelta(days=1)).isoformat()
    plans = [
        _plan("heavy", "很重的事", weight=0.9),
        _plan("ear", "问白耳洞", due=soon),
        _plan("far", "很久以后", due=later),
        _plan("overdue", "已经过了", due=late),
        _plan("today", "今天的", due=TODAY.isoformat()),
    ]

    text = surface._todo_section(plans)
    order = [line.split("[bucket_id:")[1].split("]")[0] for line in text.splitlines() if "[bucket_id:" in line]

    assert order[:3] == ["overdue", "today", "ear"]
    assert order[3] == "heavy"
    assert "⏰ 过了 1 天" in text and "⏰ 今天" in text and "⏰ 还有 4 天" in text
    assert "📅 " in text   # 远的只标日期


@pytest.mark.asyncio
async def test_plan_tool_takes_due_and_updates_it_on_repeat(monkeypatch, bucket_mgr):
    from tools.plan.core import plan_create

    class Decay:
        async def ensure_started(self):
            return None

    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr("tools.plan.summary.schedule_plan_summaries", lambda _p: None)

    out = await plan_create("问白耳洞好了没有", due="2026-10-26")
    bid = out.split("→")[1].split(" ")[0]
    assert "到期 2026-10-26" in out
    assert (await bucket_mgr.get(bid))["metadata"]["due"] == "2026-10-26"

    again = await plan_create("问白耳洞好了没有", due="2026-10-27")
    assert "到期日改成 2026-10-27" in again
    assert (await bucket_mgr.get(bid))["metadata"]["due"] == "2026-10-27"

    with pytest.raises(ToolInputError):
        await plan_create("别的事", due="下周")


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
    def __init__(self, body=None, path_params=None):
        self.method = "POST"
        self._body = body or {}
        self.path_params = path_params or {}
        self.headers = {}
        self.query_params = {}

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_dashboard_sets_and_clears_due(monkeypatch, bucket_mgr):
    import web.plans as plans_web

    monkeypatch.setattr(plans_web.sh, "_require_auth", lambda _r: None)
    monkeypatch.setattr(plans_web.sh, "bucket_mgr", bucket_mgr)
    mcp = _FakeMCP()
    plans_web.register(mcp)
    action = mcp.routes[("POST", "/api/plans/{bucket_id}/action")]
    listing = mcp.routes[("GET", "/api/plans")]

    bid = await bucket_mgr.create("问白耳洞", bucket_type="plan", name="耳洞")
    await bucket_mgr.update(bid, status="active")

    res = await action(_Req({"action": "due", "due": "2026-10-26"}, {"bucket_id": bid}))
    assert json.loads(res.body)["ok"] is True
    plans = json.loads((await listing(_Req())).body)
    assert plans["active"][0]["due"] == "2026-10-26"
    meta = (await bucket_mgr.get(bid))["metadata"]
    assert meta["change_log"][-1]["action"] == "due"

    bad = await action(_Req({"action": "due", "due": "10/26"}, {"bucket_id": bid}))
    assert bad.status_code == 400

    await action(_Req({"action": "due", "due": ""}, {"bucket_id": bid}))
    assert "due" not in (await bucket_mgr.get(bid))["metadata"]
