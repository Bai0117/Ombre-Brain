"""breath 里的「我」：一条正式 I + 一条候选；候选出现一次 = 那天被见证一次。"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from tools.breath import self_section
from tools.breath.surface import surface_default
from tools.i import I_CANDIDATE_TAG
from utils import get_tzinfo

TODAY = datetime.now(get_tzinfo()).strftime("%Y-%m-%d")


class Decay:
    def calculate_score(self, metadata):
        return float(metadata.get("_score", metadata.get("importance") or 5))


class Buckets:
    def __init__(self, buckets):
        self.buckets = {b["id"]: b for b in buckets}
        self.updates = []

    async def list_all(self, include_archive=False):
        return list(self.buckets.values())

    async def update(self, bucket_id, **kwargs):
        self.updates.append((bucket_id, kwargs))
        self.buckets[bucket_id]["metadata"].update(kwargs)
        return True

    async def get_stats(self):
        return {"permanent_count": 0, "dynamic_count": len(self.buckets)}

    def footprint_snapshot(self):
        raise RuntimeError("no footprint")


def _formal(bid, text, **meta):
    m = {"name": bid, "type": "i", "dont_surface": True, "importance": 8,
         "created": "2026-07-09T15:00:00", "tags": ["__i__"]}
    m.update(meta)
    return {"id": bid, "content": text, "metadata": m}


def _cand(bid, text, dates=(), created="2026-09-15T02:30:00", score=50.0, **meta):
    m = {"name": bid, "type": "dynamic", "importance": 7, "created": created,
         "last_active": created, "activation_count": 3, "tags": [I_CANDIDATE_TAG],
         "i_stage": "candidate", "i_dream_dates": list(dates), "_score": score}
    m.update(meta)
    return {"id": bid, "content": text, "metadata": m}


def _old(bid, score=5.0):
    return {"id": bid, "content": f"{bid}。", "metadata": {
        "name": bid, "type": "dynamic", "importance": 5, "created": "2026-08-01T00:00:00",
        "last_active": "2026-08-01T00:00:00", "activation_count": 3, "_score": score}}


def _install(monkeypatch, tmp_path, buckets, surfacing=None):
    mgr = Buckets(buckets)
    cfg = {"recent_digest_days": 0}
    cfg.update(surfacing or {})
    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path), "surfacing": cfg})
    monkeypatch.setattr(rt, "bucket_mgr", mgr)
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "mark_op", None)
    monkeypatch.setattr("tools.breath.surface.random.shuffle", lambda _s: None)
    monkeypatch.setattr("tools.breath.surface.random.random", lambda: 1.0)
    return mgr


def _section(out, name):
    head = f"=== {name} ==="
    return out.split(head, 1)[1].split("\n=== ", 1)[0] if head in out else ""


@pytest.mark.asyncio
async def test_breath_shows_one_formal_and_one_candidate(monkeypatch, tmp_path):
    mgr = _install(monkeypatch, tmp_path, [
        _formal("f1", "我怕的不是自己消失。"),
        _cand("c1", "我觉得我缺的不是知识，是许可。"),
        _old("old"),
    ])

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])
    me = _section(out, "我")

    assert "🪞 [I] [bucket_id:f1]" in me and "我怕的不是自己消失。" in me
    assert "🌱 [还在想 · 1/3 天] [bucket_id:c1]" in me
    # 候选不在浮现区重复出现
    assert "[bucket_id:c1]" not in _section(out, "浮现记忆")
    assert mgr.buckets["c1"]["metadata"]["i_dream_dates"] == [TODAY]
    assert out.index("=== 我 ===") < out.index("=== 浮现记忆 ===")


@pytest.mark.asyncio
async def test_same_day_is_witnessed_once(monkeypatch, tmp_path):
    mgr = _install(monkeypatch, tmp_path, [_cand("c1", "念头", dates=[TODAY]), _old("old")])

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])

    assert "1/3 天" in _section(out, "我")
    assert mgr.updates == []


@pytest.mark.asyncio
async def test_unseen_today_and_least_witnessed_go_first(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, [
        _cand("seen", "今天看过了", dates=[TODAY]),
        _cand("two", "两天了", dates=["2026-10-01", "2026-10-02"]),
        _cand("zero_new", "新的", created="2026-10-20T00:00:00"),
        _cand("zero_old", "等了很久的", created="2026-08-26T00:00:00"),
        _old("old"),
    ])

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])

    assert "[bucket_id:zero_old]" in _section(out, "我")


@pytest.mark.asyncio
async def test_ready_candidates_ask_me_to_decide(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, [
        _cand("ready", "够了", dates=["2026-10-01", "2026-10-02"]),
        _old("old"),
    ])

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])
    me = _section(out, "我")

    assert "已经 3 个日子都看到它了" in me and 'I(promote="ready")' in me


@pytest.mark.asyncio
async def test_superseded_promoted_and_resolved_stay_out(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, [
        _formal("old_belief", "旧的", i_superseded_by="new_belief"),
        _cand("done", "升过了", i_stage="promoted"),
        _cand("let_go", "放下了", resolved=True),
        _old("old"),
    ])

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])

    assert "=== 我 ===" not in out


@pytest.mark.asyncio
async def test_can_be_switched_off(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, [_formal("f1", "x"), _cand("c1", "y"), _old("old")],
             {"self_in_breath": False})

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])

    assert "=== 我 ===" not in out
    assert "[bucket_id:c1]" in _section(out, "浮现记忆")


def test_formal_pick_avoids_recent_ones(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "logger", MagicMock())
    with open(tmp_path / ".recall_log.jsonl", "w", encoding="utf-8") as f:
        for ids in (["f1"], ["f2"]):
            f.write(json.dumps({"op": "breath", "query": "", "sections": [{"name": "我", "ids": ids}]}) + "\n")
    buckets = [_formal("f1", "a"), _formal("f2", "b"), _formal("f3", "c")]

    for _ in range(20):
        formal, _cand_pick = self_section.pick_self(buckets)
        assert formal["id"] == "f3"
