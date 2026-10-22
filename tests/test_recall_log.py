"""他想起了什么：每次 breath 一类调用后，记下各段浮上来的 bucket_id。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

BREATH_OUT = """=== 核心准则 ===
📌 [核心准则] [bucket_id:core1] [2026-07-03 10:57]
正文
↳ 相关 → abcdef123456
---
📌 [核心准则] [bucket_id:core2] [2026-08-26 07:39]
正文

=== 待办（2 条）===
做完了 trace(bucket_id, status="resolved")
📋 [bucket_id:plan1] [2026-10-22 09:56] 问耳洞
📋 [bucket_id:plan2] [2026-10-22 09:56] 合进 main

=== 这几天 ===
最近几天存下的事……要看某天的原文用 breath_search(query="...", date_from="YYYY-MM-DD")。

📅 10.22（周四） · 11 条
白告诉我 Zeabur 要停止运营 [bucket_id:shouldnotcount]

📅 10.21（周三） · 3 条
白读完《染血之室》

=== 浮现记忆 ===
[权重:8.82] [bucket_id:m1] [2026-10-22 20:25]
正文 [bucket_id:m1] 重复出现只记一次
---
[权重:7.11] [bucket_id:m2] [2026-10-18 07:00]

=== 久未浮现 ===
💤 [久未浮现] [bucket_id:old1] [2026-08-27 05:10]
"""

SEARCH_OUT = """[bucket_id:s1] [2026-10-20 06:15]
Hill House
---
[语义关联] [bucket_id:s2] [2026-10-21 06:12]

=== 忽然想起来（非检索命中） ===
[联想浮现·非检索命中] [bucket_id:s3] [2026-07-08 13:08]
"""


def test_parse_breath_sections():
    from tools.breath.recall_log import parse_recall

    sections, days = parse_recall(BREATH_OUT)
    assert [(s["name"], s["ids"]) for s in sections] == [
        ("核心准则", ["core1", "core2"]),
        ("待办", ["plan1", "plan2"]),
        ("这几天", []),
        ("浮现记忆", ["m1", "m2"]),
        ("久未浮现", ["old1"]),
    ]
    assert days == ["10.22（周四） · 11 条", "10.21（周三） · 3 条"]


def test_parse_search_results_before_any_header():
    from tools.breath.recall_log import parse_recall

    sections, days = parse_recall(SEARCH_OUT)
    assert [(s["name"], s["ids"]) for s in sections] == [
        ("检索结果", ["s1", "s2"]),
        ("忽然想起来", ["s3"]),
    ]
    assert days == []
    assert parse_recall("没有找到匹配的记忆。") == ([], [])


@pytest.fixture
def rt_tmp(tmp_path, monkeypatch):
    from tools import _runtime as rt
    from tools.breath import recall_log

    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(recall_log, "_line_count", None)
    return tmp_path


def test_record_and_read_newest_first(rt_tmp):
    from tools.breath.recall_log import read_recall, record_recall

    record_recall("breath", {}, BREATH_OUT)
    record_recall("breath_search", {"query": "Hill House"}, SEARCH_OUT)
    record_recall("hold", {}, "📋 [bucket_id:x]")       # 不是想起，不记
    out = read_recall(10)
    assert [e["op"] for e in out] == ["breath_search", "breath"]
    assert out[0]["label"] == "搜索" and out[0]["query"] == "Hill House"
    assert out[1]["label"] == "醒来" and out[1]["days"][0].startswith("10.22")
    assert out[1]["at"].count("T") == 1


def test_log_is_trimmed(rt_tmp, monkeypatch):
    from tools.breath import recall_log

    monkeypatch.setattr(recall_log, "_KEEP", 5)
    monkeypatch.setattr(recall_log, "_TRIM_AT", 8)
    for i in range(20):
        recall_log.record_recall("breath_search", {"query": f"q{i}"}, f"[bucket_id:b{i}]")
    lines = (rt_tmp / ".recall_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert 5 <= len(lines) <= 8
    assert json.loads(lines[-1])["query"] == "q19"
    assert [e["query"] for e in recall_log.read_recall(3)] == ["q19", "q18", "q17"]


def test_bad_lines_skipped_and_failures_swallowed(rt_tmp, monkeypatch):
    from tools.breath import recall_log

    (rt_tmp / ".recall_log.jsonl").write_text('不是 json\n{"op": "breath", "at": "x", "sections": []}\n', encoding="utf-8")
    assert [e["op"] for e in recall_log.read_recall()] == ["breath"]

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom)
    recall_log.record_recall("breath", {}, BREATH_OUT)   # 不抛
    assert recall_log.rt.logger.warning.called


def test_without_buckets_dir_nothing_happens(monkeypatch):
    from tools import _runtime as rt
    from tools.breath import recall_log

    monkeypatch.setattr(rt, "config", {})
    recall_log.record_recall("breath", {}, BREATH_OUT)
    assert recall_log.read_recall() == []


@pytest.mark.asyncio
async def test_server_wrapper_records_breath_family(rt_tmp):
    """真走一遍 server._with_notice：breath 家族记下来，返回原样；别的工具不记。"""
    import server
    from tools.breath.recall_log import read_recall

    async def out(text):
        return text

    got = await server._with_notice(out(BREATH_OUT), op="breath", args={})
    assert got.startswith("=== 核心准则 ===")
    await server._with_notice(out("✓ 已存"), op="hold", args={"content": "x"})
    await server._with_notice(out(SEARCH_OUT), op="breath_search", args={"query": "鬼入侵"})
    assert [(e["op"], e["query"]) for e in read_recall()] == [("breath_search", "鬼入侵"), ("breath", "")]
