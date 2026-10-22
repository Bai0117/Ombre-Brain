"""breath 两件事：「这几天」写过的让出浮现区；小池子每次随机想起一条。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from tools.breath import keepsake as keepsake_mod
from tools.breath.surface import surface_default


class DisabledEmbedding:
    enabled = False


class WeightedDecay:
    is_running = True

    async def ensure_started(self):
        return None

    def calculate_score(self, metadata):
        return float(metadata.get("_score", metadata.get("importance") or 5))


class StubDehydrator:
    api_available = True

    async def summarize_day(self, label, entries):
        return "那天的事。"


class PlainBucketManager:
    def __init__(self, buckets):
        self.buckets = list(buckets)

    async def list_all(self, include_archive=False):
        return list(self.buckets)

    async def get_stats(self):
        return {"permanent_count": 0, "dynamic_count": len(self.buckets)}

    def footprint_snapshot(self):
        raise RuntimeError("no footprint in tests")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _bucket(bid, *, age_days=0.0, score=1.0, importance=5, arousal=0.3,
            domain=("回归测试",), **meta):
    created = datetime.now() - timedelta(days=age_days)
    m = {
        "name": bid,
        "type": "dynamic",
        "importance": importance,
        "arousal": arousal,
        "domain": list(domain),
        "created": _iso(created),
        "last_active": _iso(created),
        "activation_count": 3,
        "_score": score,
    }
    m.update(meta)
    return {"id": bid, "content": f"{bid} 的正文。", "metadata": m}


def _install(monkeypatch, tmp_path, buckets, surfacing=None):
    monkeypatch.setattr(
        rt, "config", {"surfacing": surfacing or {}, "buckets_dir": str(tmp_path)}
    )
    monkeypatch.setattr(rt, "bucket_mgr", PlainBucketManager(buckets))
    monkeypatch.setattr(rt, "decay_engine", WeightedDecay())
    monkeypatch.setattr(rt, "embedding_engine", DisabledEmbedding())
    monkeypatch.setattr(rt, "dehydrator", StubDehydrator())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "mark_op", None)
    monkeypatch.setattr("tools.breath.surface.random.shuffle", lambda _seq: None)
    monkeypatch.setattr("tools.breath.surface.random.random", lambda: 1.0)


def _section(out: str, name: str) -> str:
    head = f"=== {name} ==="
    if head not in out:
        return ""
    rest = out.split(head, 1)[1]
    return rest.split("\n=== ", 1)[0]


# ---------------- 「这几天」写过的让出浮现区 ----------------

@pytest.mark.asyncio
async def test_digest_covered_memories_leave_room_for_older_ones(monkeypatch, tmp_path):
    today = [_bucket(f"ob-{i}", age_days=0.01 * i, score=50 - i, domain=("编程",))
             for i in range(8)]
    old = [_bucket(f"old-{i}", age_days=40, score=5 - 0.1 * i) for i in range(8)]
    _install(monkeypatch, tmp_path, today + old)

    out = await surface_default(max_results=8, max_tokens=100_000, tag_filter=[])
    shown = _section(out, "浮现记忆")

    assert "=== 这几天 ===" in out
    assert sum(f"[bucket_id:ob-{i}]" in shown for i in range(8)) == 3
    assert sum(f"[bucket_id:old-{i}]" in shown for i in range(8)) == 5


@pytest.mark.asyncio
async def test_the_kept_few_prefer_her_over_engineering(monkeypatch, tmp_path):
    eng = [_bucket(f"eng-{i}", age_days=0.01 * i, score=60 - i, importance=7,
                   domain=("编程", "AI")) for i in range(5)]
    night = _bucket("night", age_days=0.3, score=10, importance=5, arousal=0.9,
                    domain=("心理",))
    body = _bucket("body", age_days=0.4, score=9, importance=7, domain=("健康",))
    old = [_bucket(f"old-{i}", age_days=40, score=5) for i in range(5)]
    _install(monkeypatch, tmp_path, eng + [night, body] + old)

    out = await surface_default(max_results=10, max_tokens=100_000, tag_filter=[])
    shown = _section(out, "浮现记忆")

    assert "[bucket_id:night]" in shown
    assert "[bucket_id:body]" in shown
    assert sum(f"[bucket_id:eng-{i}]" in shown for i in range(5)) == 1


@pytest.mark.asyncio
async def test_dropped_recent_ones_do_not_come_back_as_long_unseen(monkeypatch, tmp_path):
    fresh = [_bucket(f"new-{i}", age_days=1.5 + 0.01 * i, score=20 - i,
                     importance=9, activation_count=0) for i in range(6)]
    _install(monkeypatch, tmp_path, fresh)

    out = await surface_default(max_results=10, max_tokens=100_000, tag_filter=[])

    assert "=== 久未浮现 ===" not in out
    assert sum(f"[bucket_id:new-{i}]" in out.split("=== 这几天 ===")[0] for i in range(6)) == 0


@pytest.mark.asyncio
async def test_negative_setting_turns_the_cap_off(monkeypatch, tmp_path):
    today = [_bucket(f"ob-{i}", age_days=0.01 * i, score=50 - i) for i in range(6)]
    _install(monkeypatch, tmp_path, today, {"recent_in_surface": -1})

    out = await surface_default(max_results=10, max_tokens=100_000, tag_filter=[])

    assert all(f"[bucket_id:ob-{i}]" in _section(out, "浮现记忆") for i in range(6))


@pytest.mark.asyncio
async def test_without_the_digest_nothing_is_capped(monkeypatch, tmp_path):
    today = [_bucket(f"ob-{i}", age_days=0.01 * i, score=50 - i) for i in range(6)]
    _install(monkeypatch, tmp_path, today, {"recent_digest_days": 0})

    out = await surface_default(max_results=10, max_tokens=100_000, tag_filter=[])

    assert "=== 这几天 ===" not in out
    assert all(f"[bucket_id:ob-{i}]" in out for i in range(6))


# ---------------- 小池子 ----------------

@pytest.mark.asyncio
async def test_one_keepsake_surfaces_in_its_own_section(monkeypatch, tmp_path):
    old = [_bucket(f"old-{i}", age_days=40, score=50 - i) for i in range(5)]
    pool = [_bucket(f"bella-{i}", age_days=60, score=0.1, keepsake=True) for i in range(3)]
    _install(monkeypatch, tmp_path, old + pool)

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])
    section = _section(out, "小池子")

    assert section
    assert sum(f"[bucket_id:bella-{i}]" in section for i in range(3)) == 1
    assert "🫧 [小池子]" in section
    # 不占浮现名额
    assert all(f"[bucket_id:old-{i}]" in _section(out, "浮现记忆") for i in range(5))


@pytest.mark.asyncio
async def test_resolved_or_digested_keepsakes_still_come_up(monkeypatch, tmp_path):
    old = [_bucket("old", age_days=40, score=5)]
    pool = [_bucket("done", age_days=60, resolved=True, digested=True, keepsake=True)]
    _install(monkeypatch, tmp_path, old + pool)

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])

    assert "[bucket_id:done]" in _section(out, "小池子")


@pytest.mark.asyncio
async def test_forgotten_letters_and_archived_stay_out(monkeypatch, tmp_path):
    old = [_bucket("old", age_days=40, score=5)]
    pool = [
        _bucket("faded", age_days=60, keepsake=True, dont_surface=True),
        _bucket("letter", age_days=60, keepsake=True, type="letter"),
        _bucket("gone", age_days=60, keepsake=True, type="archived"),
    ]
    _install(monkeypatch, tmp_path, old + pool)

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])

    assert "=== 小池子 ===" not in out


@pytest.mark.asyncio
async def test_a_keepsake_already_surfacing_is_not_repeated(monkeypatch, tmp_path):
    only = _bucket("bella", age_days=40, score=50, keepsake=True)
    _install(monkeypatch, tmp_path, [only])

    out = await surface_default(max_results=5, max_tokens=100_000, tag_filter=[])

    assert "[bucket_id:bella]" in _section(out, "浮现记忆")
    assert "=== 小池子 ===" not in out


@pytest.mark.asyncio
async def test_date_scoped_breath_has_no_pool(monkeypatch, tmp_path):
    old = [_bucket("old", age_days=40, score=5)]
    pool = [_bucket("bella", age_days=60, keepsake=True)]
    _install(monkeypatch, tmp_path, old + pool)

    out = await surface_default(
        max_results=5, max_tokens=100_000, tag_filter=[],
        created_from=datetime.now() - timedelta(days=50),
    )

    assert "=== 小池子 ===" not in out


def _log(tmp_path, *picked_rounds):
    with open(tmp_path / ".recall_log.jsonl", "w", encoding="utf-8") as f:
        for ids in picked_rounds:
            f.write(json.dumps({
                "at": "2026-10-22T22:00:00+08:00", "op": "breath", "query": "",
                "sections": [{"name": "小池子", "ids": list(ids)}], "days": [],
            }, ensure_ascii=False) + "\n")


def test_recently_picked_ones_are_avoided(monkeypatch, tmp_path):
    pool = [_bucket(f"k-{i}", keepsake=True) for i in range(4)]
    monkeypatch.setattr(rt, "config", {"surfacing": {}, "buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "logger", MagicMock())
    _log(tmp_path, ["k-0"], ["k-1"], ["k-2"])

    for _ in range(30):
        picks = keepsake_mod.pick_keepsakes(pool, set())
        assert [b["id"] for b in picks] == ["k-3"]


def test_a_tiny_pool_still_gives_something(monkeypatch, tmp_path):
    pool = [_bucket("k-0", keepsake=True)]
    monkeypatch.setattr(rt, "config", {"surfacing": {}, "buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "logger", MagicMock())
    _log(tmp_path, ["k-0"], ["k-0"])

    assert [b["id"] for b in keepsake_mod.pick_keepsakes(pool, set())] == ["k-0"]


def test_per_breath_setting(monkeypatch, tmp_path):
    pool = [_bucket(f"k-{i}", keepsake=True) for i in range(5)]
    monkeypatch.setattr(
        rt, "config", {"surfacing": {"keepsake_per_breath": 2}, "buckets_dir": str(tmp_path)}
    )
    monkeypatch.setattr(rt, "logger", MagicMock())

    assert len(keepsake_mod.pick_keepsakes(pool, set())) == 2
    monkeypatch.setattr(
        rt, "config", {"surfacing": {"keepsake_per_breath": 0}, "buckets_dir": str(tmp_path)}
    )
    assert keepsake_mod.pick_keepsakes(pool, set()) == []


def test_recall_log_reads_the_pool_section():
    from tools.breath.recall_log import parse_breath_blocks, parse_recall

    text = (
        "=== 浮现记忆 ===\n[权重:3.00] [bucket_id:a1] [2026-10-01 10:00]\n正文\n\n"
        "=== 小池子 ===\n🫧 [小池子] [bucket_id:b2] [2026-09-25 04:02]\n颠茄"
    )
    sections, _ = parse_recall(text)
    assert {"name": "小池子", "ids": ["b2"]} in sections
    blocks = parse_breath_blocks(text)
    pool = [s for s in blocks if s["name"] == "小池子"][0]
    assert pool["items"][0]["tag"] == "小池子"
    assert pool["items"][0]["body"] == "颠茄"


# ---------------- 后台按钮：/api/bucket/{id}/keepsake ----------------

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
    def __init__(self, body=None, bucket_id=""):
        self._body = body or {}
        self.path_params = {"bucket_id": bucket_id}
        self.headers = {}
        self.query_params = {}
        self.method = "POST"

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_keepsake_button_round_trip(monkeypatch, bucket_mgr):
    import web.buckets as buckets_web

    monkeypatch.setattr(buckets_web.sh, "_require_auth", lambda _r: None)
    monkeypatch.setattr(buckets_web.sh, "bucket_mgr", bucket_mgr)
    mcp = _FakeMCP()
    buckets_web.register(mcp)
    toggle = mcp.routes[("POST", "/api/bucket/{bucket_id}/keepsake")]

    bid = await bucket_mgr.create("颠茄爱月光。", name="颠茄", domain=["恋爱"])

    res = await toggle(_Req({}, bid))
    assert json.loads(res.body)["keepsake"] is True
    assert (await bucket_mgr.get(bid))["metadata"]["keepsake"] is True

    res = await toggle(_Req({"keepsake": True}, bid))
    assert json.loads(res.body)["keepsake"] is True

    res = await toggle(_Req({"keepsake": False}, bid))
    assert json.loads(res.body)["keepsake"] is False
    assert "keepsake" not in (await bucket_mgr.get(bid))["metadata"]


@pytest.mark.asyncio
async def test_plans_cannot_go_in_the_pool(monkeypatch, bucket_mgr):
    import web.buckets as buckets_web

    monkeypatch.setattr(buckets_web.sh, "_require_auth", lambda _r: None)
    monkeypatch.setattr(buckets_web.sh, "bucket_mgr", bucket_mgr)
    mcp = _FakeMCP()
    buckets_web.register(mcp)
    toggle = mcp.routes[("POST", "/api/bucket/{bucket_id}/keepsake")]

    bid = await bucket_mgr.create("记得问耳洞。", bucket_type="plan", name="待办")
    res = await toggle(_Req({}, bid))
    assert res.status_code == 400
    assert "keepsake" not in (await bucket_mgr.get(bid))["metadata"]


def test_footprint_names_the_pool():
    from ombrebrain.eventsourcing.footprint import _event_label

    put = {"event_type": "TraceUpdated",
           "payload": {"changed_fields": ["keepsake"], "keepsake": True}}
    out = {"event_type": "TraceUpdated", "payload": {"changed_fields": ["keepsake"]}}
    assert _event_label(put) == "放进小池子"
    assert _event_label(out) == "拿出小池子"
