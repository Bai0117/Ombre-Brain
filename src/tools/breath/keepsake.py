"""
========================================
tools/breath/keepsake.py — 小池子
========================================

白说：有一些比较重要的小小事，想让它们常常随机浮上来，比如我们的颠茄。

为什么不加权重：权重一高，它就每次都排在浮现区前面，跟新记忆一样把位置
占掉，也就不是「随机想起」了。钉成核心准则也不对——那是每次都必须读的规矩。

所以单独一个小池子：
- 记忆的 frontmatter 带 keepsake: true 就在池子里。她在后台点按钮放进去、
  拿出来（/api/bucket/{id}/keepsake），我也可以。
- 每次无参 breath 从池子里随机抽 surfacing.keepsake_per_breath 条（默认 1），
  单独一段「小池子」，不占浮现记忆的名额。
- 躲开最近几次醒来已经从池子里想起过的（读「他想起的」的记录），
  池子小的时候躲得少一点，免得抽不出来。
- 已经出现在核心准则 / 浮现区里的这次不再抽。
- 她亲手放进去的就算「已解决」「已消化」也照样会想起；
  主动遗忘、归档、删除、信不进池子。

对外暴露：is_keepsake / keepsake_pool / pick_keepsakes / recently_picked
========================================
"""

from __future__ import annotations

import random

from .. import _runtime as rt
from .._common import is_terminal_memory_metadata
from ..plan.core import is_letter_bucket
from utils import parse_bool

SECTION = "小池子"
_PER_BREATH_DEFAULT = 1
_AVOID_RECENT_MAX = 5  # 最多躲开最近几次醒来抽到的


def is_keepsake(bucket: dict) -> bool:
    return parse_bool((bucket.get("metadata") or {}).get("keepsake"), default=False)


def _eligible(bucket: dict) -> bool:
    meta = bucket.get("metadata") or {}
    if not is_keepsake(bucket):
        return False
    if is_letter_bucket(bucket) or is_terminal_memory_metadata(meta):
        return False
    if parse_bool(meta.get("dont_surface"), default=False):
        return False
    if str(meta.get("type") or "").strip().lower() in ("plan", "letter"):
        return False
    return True


def keepsake_pool(all_buckets: list[dict]) -> list[dict]:
    return [b for b in all_buckets if _eligible(b)]


def _per_breath() -> int:
    cfg = (rt.config or {}).get("surfacing", {}) or {}
    try:
        return max(0, int(cfg.get("keepsake_per_breath", _PER_BREATH_DEFAULT)))
    except (TypeError, ValueError):
        return _PER_BREATH_DEFAULT


def _recently_picked(times: int, section: str = SECTION) -> set[str]:
    """最近 times 次无参 breath 在某一段（默认「小池子」）里出现过的 id。"""
    if times <= 0:
        return set()
    try:
        from .recall_log import read_recall

        seen: set[str] = set()
        n = 0
        for entry in read_recall(limit=60):
            if entry.get("op") != "breath" or entry.get("query"):
                continue
            n += 1
            for sec in entry.get("sections") or []:
                if sec.get("name") == section:
                    seen.update(sec.get("ids") or [])
            if n >= times:
                break
        return seen
    except Exception as e:  # 读不到记录就当没躲，不能连累 breath
        rt.logger.warning(f"keepsake: recall log unavailable: {e}")
        return set()


def pick_keepsakes(all_buckets: list[dict], exclude_ids: set[str]) -> list[dict]:
    """这次 breath 从小池子里想起哪几条（随机，躲开刚想起过的）。"""
    k = _per_breath()
    if k <= 0:
        return []
    pool = [b for b in keepsake_pool(all_buckets) if b["id"] not in exclude_ids]
    if not pool:
        return []
    # 池子里有 n 条、每次抽 k 条，最多能躲 (n-1)//k 次而不抽空
    avoid_times = min(_AVOID_RECENT_MAX, max(0, (len(pool) - 1) // k))
    recent = _recently_picked(avoid_times)
    fresh = [b for b in pool if b["id"] not in recent]
    picks = random.sample(fresh, min(k, len(fresh)))
    if len(picks) < k:
        rest = [b for b in pool if b not in picks]
        picks += random.sample(rest, min(k - len(picks), len(rest)))
    return picks


recently_picked = _recently_picked
