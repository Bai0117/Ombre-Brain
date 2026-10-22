"""No-argument breath lists active plans as one-line to-dos after the core rules."""

from __future__ import annotations

import pytest


def _core(bucket_id, content):
    return {
        "id": bucket_id,
        "content": content,
        "metadata": {"type": "permanent", "importance": 10, "pinned": True, "domain": []},
    }


def _ordinary(bucket_id, content):
    return {
        "id": bucket_id,
        "content": content,
        "metadata": {"type": "dynamic", "importance": 8, "activation_count": 1, "domain": []},
    }


def _plan(bucket_id, content, *, status="active", weight=0.5, created="2026-10-22T08:00:00+08:00"):
    return {
        "id": bucket_id,
        "content": content,
        "metadata": {
            "type": "plan", "status": status, "weight": weight, "created": created,
            "domain": ["plan"], "tags": ["__plan__"], "importance": 7,
        },
    }


async def _breath(buckets, monkeypatch, max_tokens=20000):
    from tests.test_breath_verbatim_patch import OrderedBucketManager, _install_runtime
    from tools.breath.surface import surface_default

    manager = OrderedBucketManager(buckets)

    async def get_stats():
        kinds = [b["metadata"].get("type") for b in buckets]
        return {"permanent_count": kinds.count("permanent"), "dynamic_count": kinds.count("dynamic")}

    manager.get_stats = get_stats
    _install_runtime(manager)
    monkeypatch.setattr("tools.breath.surface.random.random", lambda: 1.0)
    return await surface_default(max_results=5, max_tokens=max_tokens, tag_filter=[])


@pytest.mark.asyncio
async def test_active_plans_are_listed_after_core_rules(monkeypatch):
    out = await _breath([
        _core("core", "核心准则正文"),
        _ordinary("mem", "普通记忆正文"),
        _plan("p1", "10.26 左右问她耳洞有没有好转"),
        _plan("done", "已经做完的事", status="resolved"),
        _plan("gaveup", "不做了的事", status="abandoned"),
    ], monkeypatch)

    assert "=== 待办（1 条）===" in out
    assert "📋 [bucket_id:p1] [2026-10-22 08:00] 10.26 左右问她耳洞有没有好转" in out
    assert "已经做完的事" not in out and "不做了的事" not in out
    assert out.index("=== 核心准则 ===") < out.index("=== 待办") < out.index("=== 浮现记忆 ===")


@pytest.mark.asyncio
async def test_heavier_and_older_plans_come_first_and_long_text_is_trimmed(monkeypatch):
    out = await _breath([
        _ordinary("mem", "普通记忆"),
        _plan("light", "轻的", weight=0.2, created="2026-10-01T00:00:00+08:00"),
        _plan("heavy-new", "重的新的", weight=0.9, created="2026-10-20T00:00:00+08:00"),
        _plan("heavy-old", "重的旧的", weight=0.9, created="2026-10-02T00:00:00+08:00"),
        _plan("long", "很长" * 80, weight=0.1),
    ], monkeypatch)

    order = [out.index(f"[bucket_id:{i}]") for i in ("heavy-old", "heavy-new", "light", "long")]
    assert order == sorted(order)
    long_line = next(line for line in out.splitlines() if "[bucket_id:long]" in line)
    assert long_line.endswith("…") and long_line.count("很长") < 80


@pytest.mark.asyncio
async def test_more_than_the_cap_points_to_the_full_list(monkeypatch):
    plans = [_plan(f"p{i:02d}", f"事情 {i}", created=f"2026-10-{i + 1:02d}T00:00:00+08:00") for i in range(15)]
    out = await _breath([_ordinary("mem", "普通记忆")] + plans, monkeypatch)

    assert "=== 待办（15 条）===" in out
    assert out.count("📋 [bucket_id:") == 12
    assert "另有 3 条" in out


@pytest.mark.asyncio
async def test_to_dos_show_even_when_nothing_else_surfaces(monkeypatch):
    out = await _breath([_plan("p1", "唯一的待办")], monkeypatch)

    assert "=== 待办（1 条）===" in out
    assert "唯一的待办" in out
    assert out.index("=== 待办") < out.index("记忆池现在是空的")


@pytest.mark.asyncio
async def test_no_section_without_active_plans(monkeypatch):
    out = await _breath([_ordinary("mem", "普通记忆"), _plan("x", "完成了", status="resolved")], monkeypatch)

    assert "待办" not in out
