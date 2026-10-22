"""breath(domain="plan") / feel 通道的条目头：时间与无参 breath 同格式、同时区。

以前这里直接放 created 原值；VPS 容器是 UTC，读出来是
`[2026-10-22T01:56:35+00:00]`，而首页同一条待办写的是 `[2026-10-22 09:56]`。
"""

from __future__ import annotations

from datetime import timedelta, timezone

import pytest


def _bucket(bucket_id, created):
    meta = {"type": "plan", "status": "active", "domain": ["plan"], "tags": ["__plan__"]}
    if created is not None:
        meta["created"] = created
    return {"id": bucket_id, "content": "某件待办", "metadata": meta}


@pytest.fixture
def render(monkeypatch):
    import utils
    from tools.breath._shared import render_within_budget

    monkeypatch.setattr(utils, "get_tzinfo", lambda: timezone(timedelta(hours=8)))

    def run(*buckets):
        lines, omitted = render_within_budget(list(buckets), 10_000, lambda _b: "")
        assert omitted == 0
        return lines

    return run


def test_utc_timestamp_is_shown_in_local_time(render):
    (entry,) = render(_bucket("p1", "2026-10-22T01:56:35+00:00"))
    assert entry.startswith("[2026-10-22 09:56] [bucket_id:p1]")
    assert "+00:00" not in entry


def test_naive_legacy_timestamp_keeps_its_wall_clock(render):
    (entry,) = render(_bucket("p1", "2026-07-03T10:57:00"))
    assert entry.startswith("[2026-07-03 10:57] [bucket_id:p1]")


def test_unparsable_timestamp_is_still_shown_verbatim(render):
    (entry,) = render(_bucket("p1", "去年夏天"))
    assert entry.startswith("[去年夏天] [bucket_id:p1]")


def test_missing_timestamp_leaves_no_empty_brackets(render):
    (entry,) = render(_bucket("p1", None))
    assert entry.startswith("[bucket_id:p1]")
    assert "[]" not in entry
