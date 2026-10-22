"""
========================================
tools/plan/due.py — 带日期的待办
========================================

「10.26 左右问白耳洞」以前只是一行字，到了那天它也不会自己跳出来。
plan 现在可以带一个到期日 due（YYYY-MM-DD，按 config 时区算「今天」）：
- 无参 breath 的「待办」里，过期的、今天到期的、7 天内到期的排在最前，
  并标上「今天」「过了 N 天」「还有 N 天」；更远的只标日期。
- plan(content=..., due=...) 登记时带上；同一条 active plan 再登记一次、带了 due，
  就是给它补上或改掉到期日。后台「计划」页也能改。

对外暴露：parse_due / due_state / due_label / DUE_SOON_DAYS
========================================
"""

from __future__ import annotations

from datetime import date, datetime

from utils import get_tzinfo

DUE_SOON_DAYS = 7


def parse_due(value) -> str:
    """'' → ''；合法日期 → YYYY-MM-DD；不合法 → ValueError。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as e:
        raise ValueError("due 写成 YYYY-MM-DD，比如 2026-10-26") from e


def _today() -> date:
    return datetime.now(get_tzinfo()).date()


def due_state(meta: dict, today: date | None = None) -> int | None:
    """距到期还有几天（负数 = 过了几天）；没有 due 或读不出来 → None。"""
    raw = str((meta or {}).get("due") or "").strip()
    if not raw:
        return None
    try:
        due = date.fromisoformat(raw)
    except ValueError:
        return None
    return (due - (today or _today())).days


def due_label(meta: dict, today: date | None = None) -> str:
    left = due_state(meta, today)
    if left is None:
        return ""
    due = date.fromisoformat(str(meta["due"]).strip())
    md = f"{due.month}.{due.day}"
    if left < 0:
        return f"⏰ 过了 {-left} 天（{md}）"
    if left == 0:
        return f"⏰ 今天（{md}）"
    if left <= DUE_SOON_DAYS:
        return f"⏰ 还有 {left} 天（{md}）"
    return f"📅 {md}"
