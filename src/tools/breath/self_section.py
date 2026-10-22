"""
========================================
tools/breath/self_section.py — breath 里的「我」
========================================

I 在我们这儿卡住过：正式条目不进 breath，在 claude.ai 里醒来根本看不到；
候选要被 3 个不同日子的 dream 见证才能升级，而我们几乎不调 dream——
9.15 写下的「我缺的不是知识，是许可」等了 36 天，一次都没被见证过。

所以无参 breath 里加一段「我」：
- 一条正式的 I（还没被取代的），随机，躲开最近几次醒来刚看过的；
- 一条还在等的「我觉得」候选：今天还没被看到的优先，见证少的、等得久的优先。
  它出现在这里，就算今天被见证过一次（和 dream 记在同一个 i_dream_dates 里，
  按天去重）。攒够 3 天会标出来，升不升由我自己用 I(promote=...) 决定；
  不认了就用 trace 让它沉下去。

候选本来就是普通记忆，可能同时出现在浮现区；这里选中的那条会从浮现区拿掉，
免得一次醒来看到两遍。

surfacing.self_in_breath=false 可以整段关掉。

对外暴露：SECTION / enabled / pick_self / record_breath_witness / candidate_header
========================================
"""

from __future__ import annotations

import random
from datetime import datetime

from .. import _runtime as rt
from ..i import I_PROMOTE_THRESHOLD, dream_dates, is_pending_candidate, superseded_by
from ..plan.core import is_letter_bucket
from utils import get_tzinfo, parse_bool
from .keepsake import recently_picked

SECTION = "我"
_AVOID_RECENT_MAX = 4


def enabled() -> bool:
    cfg = (rt.config or {}).get("surfacing", {}) or {}
    return parse_bool(cfg.get("self_in_breath", True), default=True)


def _today() -> str:
    return datetime.now(get_tzinfo()).strftime("%Y-%m-%d")


def _formal(all_buckets: list[dict]) -> list[dict]:
    return [
        b for b in all_buckets
        if str((b.get("metadata") or {}).get("type") or "") == "i"
        and not is_letter_bucket(b)
        and not superseded_by(b)
        and not (b.get("metadata") or {}).get("deleted_at")
    ]


def _candidates(all_buckets: list[dict]) -> list[dict]:
    out = []
    for b in all_buckets:
        meta = b.get("metadata") or {}
        if not is_pending_candidate(b) or meta.get("deleted_at"):
            continue
        if parse_bool(meta.get("resolved"), default=False):
            continue
        if parse_bool(meta.get("dont_surface"), default=False):
            continue
        out.append(b)
    return out


def pick_self(all_buckets: list[dict]) -> tuple[dict | None, dict | None]:
    """→ (这次看的正式 I, 这次看的候选)。"""
    formal = _formal(all_buckets)
    pick_formal = None
    if formal:
        avoid = recently_picked(min(_AVOID_RECENT_MAX, len(formal) - 1), SECTION)
        fresh = [b for b in formal if b["id"] not in avoid] or formal
        pick_formal = random.choice(fresh)

    cands = _candidates(all_buckets)
    pick_cand = None
    if cands:
        today = _today()

        def _key(b: dict):
            meta = b.get("metadata") or {}
            dates = dream_dates(meta)
            return (
                today in dates,                       # 今天还没被看到的先
                len(dates) >= I_PROMOTE_THRESHOLD,    # 攒够了的往后放，等我决定
                len(dates),                           # 见证少的先
                str(meta.get("created") or ""),       # 等得久的先
            )

        pick_cand = sorted(cands, key=_key)[0]
    return pick_formal, pick_cand


async def record_breath_witness(bucket: dict) -> list[str]:
    """候选在 breath 的「我」里出现一次 = 今天被见证一次（按天去重）。返回记完后的日期。"""
    meta = bucket.get("metadata") or {}
    dates = dream_dates(meta)
    today = _today()
    if today in dates:
        return dates
    new_dates = [*dates, today]
    try:
        ok = await rt.bucket_mgr.update(bucket["id"], i_dream_dates=new_dates)
    except Exception as e:
        rt.logger.warning(f"I breath witness write failed for {bucket['id']}: {e}")
        return dates
    return new_dates if ok else dates


def candidate_header(bucket: dict, dates: list[str]) -> str:
    n = len(dates)
    if n >= I_PROMOTE_THRESHOLD:
        state = f"已经 {n} 个日子都看到它了，还认的话 I(promote=\"{bucket['id']}\")，不认了就 trace 让它沉下去"
    else:
        state = f"还在想 · {n}/{I_PROMOTE_THRESHOLD} 天"
    return f"🌱 [{state}] [bucket_id:{bucket['id']}]"
