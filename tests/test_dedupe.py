"""疑似重复：全库比向量列出对子；「不是一回事」记下来；hold 新建时提醒补旧条。"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import numpy as np
import pytest

import tools._runtime as rt
from ombrebrain.storage.vector_codec import encode_vector
from tools import dedupe


def _mem(bid, content, created, **meta):
    m = {"name": bid, "type": "dynamic", "importance": 5, "domain": ["恋爱"], "created": created}
    m.update(meta)
    return {"id": bid, "content": content, "metadata": m}


class FakeBuckets:
    def __init__(self, buckets):
        self.buckets = {b["id"]: b for b in buckets}

    async def list_all(self, include_archive=False):
        return list(self.buckets.values())

    async def get(self, bid):
        return self.buckets.get(bid)


class FakeEngine:
    enabled = True

    def __init__(self, db_path, query_hits=None):
        self.db_path = db_path
        self.query_hits = query_hits or []

    async def search_similar(self, query, top_k=10):
        return list(self.query_hits)


def _db(tmp_path, vectors: dict[str, list[float]]) -> str:
    path = str(tmp_path / "embeddings.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS embeddings (bucket_id TEXT PRIMARY KEY, embedding BLOB)")
    for bid, v in vectors.items():
        conn.execute("INSERT INTO embeddings VALUES (?, ?)", (bid, encode_vector(np.asarray(v, dtype=np.float32))))
    conn.commit()
    conn.close()
    return path


def _install(monkeypatch, tmp_path, buckets, vectors, query_hits=None):
    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "bucket_mgr", FakeBuckets(buckets))
    monkeypatch.setattr(rt, "embedding_engine", FakeEngine(_db(tmp_path, vectors), query_hits))
    monkeypatch.setattr(rt, "logger", MagicMock())


def _unit(*xs):
    v = np.asarray(xs, dtype=np.float64)
    return list(v / np.linalg.norm(v))


@pytest.mark.asyncio
async def test_similar_pairs_are_listed_older_first(monkeypatch, tmp_path):
    buckets = [
        _mem("new", "白的审美偏好：银灰、玻璃。", "2026-10-22T09:00:00"),
        _mem("old", "白喜欢银灰黑白和液态玻璃。", "2026-10-22T01:00:00"),
        _mem("far", "白读完了 Ada。", "2026-10-17T07:00:00"),
    ]
    vectors = {"new": _unit(1, 0.05, 0), "old": _unit(1, 0, 0), "far": _unit(0, 1, 0)}
    _install(monkeypatch, tmp_path, buckets, vectors)

    out = await dedupe.find_pairs(0.88)

    assert out["total"] == 1
    pair = out["pairs"][0]
    assert (pair["a"]["id"], pair["b"]["id"]) == ("old", "new")
    assert pair["kind"] == "vector"
    assert pair["score"] > 0.99
    assert pair["a"]["content"] == "白喜欢银灰黑白和液态玻璃。"


@pytest.mark.asyncio
async def test_threshold_controls_what_counts(monkeypatch, tmp_path):
    buckets = [_mem("x", "一", "2026-10-01T00:00:00"), _mem("y", "二", "2026-10-02T00:00:00")]
    vectors = {"x": _unit(1, 0, 0), "y": _unit(1, 0.5, 0)}   # cos ≈ 0.894
    _install(monkeypatch, tmp_path, buckets, vectors)

    assert (await dedupe.find_pairs(0.92))["total"] == 0
    assert (await dedupe.find_pairs(0.88))["total"] == 1


@pytest.mark.asyncio
async def test_dismissed_pairs_stay_gone(monkeypatch, tmp_path):
    buckets = [_mem("x", "一", "2026-10-01T00:00:00"), _mem("y", "二", "2026-10-02T00:00:00")]
    _install(monkeypatch, tmp_path, buckets, {"x": _unit(1, 0), "y": _unit(1, 0.01)})

    dedupe.dismiss_pair("y", "x")

    assert (await dedupe.find_pairs(0.88))["total"] == 0
    saved = json.loads((tmp_path / ".dupe_dismissed.json").read_text(encoding="utf-8"))
    assert "x|y" in saved


@pytest.mark.asyncio
async def test_letters_plans_feels_archived_and_two_core_rules_are_left_alone(monkeypatch, tmp_path):
    same = _unit(1, 0, 0)
    buckets = [
        _mem("a", "同一件事", "2026-10-01T00:00:00"),
        _mem("letter", "同一件事", "2026-10-02T00:00:00", type="letter"),
        _mem("plan", "同一件事", "2026-10-02T00:00:00", type="plan"),
        _mem("feel", "同一件事", "2026-10-02T00:00:00", type="feel"),
        _mem("gone", "同一件事", "2026-10-02T00:00:00", type="archived"),
        _mem("core1", "准则", "2026-07-01T00:00:00", type="permanent", pinned=True),
        _mem("core2", "准则", "2026-07-02T00:00:00", type="permanent", pinned=True),
    ]
    vectors = {b["id"]: same for b in buckets}
    vectors["core1"] = vectors["core2"] = _unit(0, 1, 0)
    _install(monkeypatch, tmp_path, buckets, vectors)

    out = await dedupe.find_pairs(0.88)

    assert out["total"] == 0


@pytest.mark.asyncio
async def test_a_core_rule_and_its_copy_are_listed_with_the_core_marked(monkeypatch, tmp_path):
    buckets = [
        _mem("core", "Luar，月光。", "2026-07-16T00:00:00", type="permanent", pinned=True),
        _mem("copy", "她叫我 Luar，月光。", "2026-10-01T00:00:00"),
    ]
    _install(monkeypatch, tmp_path, buckets, {"core": _unit(1, 0), "copy": _unit(1, 0.02)})

    pair = (await dedupe.find_pairs(0.88))["pairs"][0]

    assert pair["a"]["id"] == "core" and pair["a"]["pinned"] is True
    assert pair["b"]["pinned"] is False


@pytest.mark.asyncio
async def test_memories_without_a_vector_fall_back_to_text(monkeypatch, tmp_path):
    text = "白的呕吐规律已经确认：两次都是吃饭时同时灌大量液体，吃饭和喝东西要分开。"
    buckets = [
        _mem("with", text, "2026-10-22T01:45:00"),
        _mem("without", text + "继续吐就去看医生。", "2026-10-22T09:45:00"),
        _mem("other", "白玩完了 Edith Finch，最喜欢 Lewis。", "2026-10-22T01:45:00"),
    ]
    _install(monkeypatch, tmp_path, buckets, {"with": _unit(1, 0), "other": _unit(0, 1)})

    out = await dedupe.find_pairs(0.88)

    assert out["total"] == 1
    assert out["pairs"][0]["kind"] == "text"
    assert {out["pairs"][0]["a"]["id"], out["pairs"][0]["b"]["id"]} == {"with", "without"}


@pytest.mark.asyncio
async def test_hold_hint_names_the_old_memory(monkeypatch, tmp_path):
    buckets = [_mem("old", "旧的那条", "2026-10-01T00:00:00", name="白的审美偏好")]
    _install(monkeypatch, tmp_path, buckets, {}, query_hits=[("new", 1.0), ("old", 0.93)])

    hint = await dedupe.similar_to_new("new", "白喜欢银灰")

    assert "[bucket_id:old]" in hint and "白的审美偏好" in hint and "0.93" in hint


@pytest.mark.asyncio
async def test_hold_hint_stays_quiet_when_nothing_is_close_or_it_was_dismissed(monkeypatch, tmp_path):
    buckets = [_mem("old", "旧的那条", "2026-10-01T00:00:00")]
    _install(monkeypatch, tmp_path, buckets, {}, query_hits=[("old", 0.80)])
    assert await dedupe.similar_to_new("new", "x") == ""

    _install(monkeypatch, tmp_path, buckets, {}, query_hits=[("old", 0.95)])
    dedupe.dismiss_pair("new", "old")
    assert await dedupe.similar_to_new("new", "x") == ""


@pytest.mark.asyncio
async def test_hold_hint_never_breaks_hold(monkeypatch, tmp_path):
    class Broken(FakeEngine):
        async def search_similar(self, query, top_k=10):
            raise RuntimeError("WARP down")

    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "embedding_engine", Broken(""))
    monkeypatch.setattr(rt, "logger", MagicMock())

    assert await dedupe.similar_to_new("new", "x") == ""


# ---------------- API ----------------

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
    def __init__(self, body=None, query=None):
        self._body = body or {}
        self.path_params = {}
        self.headers = {}
        self.query_params = query or {}
        self.method = "GET"

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_api_lists_and_dismisses(monkeypatch, tmp_path):
    import web.search as search_web

    buckets = [_mem("x", "一", "2026-10-01T00:00:00"), _mem("y", "二", "2026-10-02T00:00:00")]
    _install(monkeypatch, tmp_path, buckets, {"x": _unit(1, 0), "y": _unit(1, 0.01)})
    monkeypatch.setattr(search_web.sh, "_require_auth", lambda _r: None)
    monkeypatch.setattr(search_web.sh, "deletion_requests", None)
    mcp = _FakeMCP()
    search_web.register(mcp)
    get = mcp.routes[("GET", "/api/duplicates")]
    dismiss = mcp.routes[("POST", "/api/duplicates/dismiss")]

    first = json.loads((await get(_Req(query={"min": "0.9"}))).body)
    assert first["total"] == 1 and first["min_score"] == 0.9

    res = await dismiss(_Req({"a": "x", "b": "y"}))
    assert json.loads(res.body)["ok"] is True
    assert json.loads((await get(_Req())).body)["total"] == 0

    bad = await dismiss(_Req({"a": "x", "b": "x"}))
    assert bad.status_code == 400
    assert (await get(_Req(query={"min": "abc"}))).status_code == 400
