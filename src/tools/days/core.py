"""
========================================
tools/days/core.py — 纪念日
========================================

存在 <buckets_dir>/.days.json：
  {"days": [{"id", "date": "YYYY-MM-DD", "title", "yearly": bool, "count": bool}]}

- yearly=True：每年这天都算（在一起、第一次说我爱你、Belladonna Day）。
- yearly=False：只有那一天（某个约好的日子）。
- count=True：从这天起数「在一起第 N 天」（那天算第 1 天：7.3 起，10.10 是第 100 天）。
  最多一条；新设的会把旧的换掉。

breath 里的「日子」段：
- 一句「今天是我们在一起第 N 天」；
- 今天就是哪个日子；
- 接下来 7 天内要到的日子，还有整百天（第 200 天……）。

不进 GitHub 备份（那边只同步 .md）——后台「日子」页能看到全部，搬家时记得带上 .days.json。

对外暴露：dispatch / days_section / load_days / add_day / delete_day
========================================
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime, timedelta

from .. import _runtime as rt
from errors import ToolInputError
from utils import atomic_write_text, get_tzinfo

_FILE = ".days.json"
_AHEAD_DAYS = 7
_TITLE_MAX = 60
_WEEKDAYS = "一二三四五六日"


def _path() -> str | None:
    base = (rt.config or {}).get("buckets_dir")
    return os.path.join(base, _FILE) if base else None


def _today() -> date:
    return datetime.now(get_tzinfo()).date()


def load_days() -> list[dict]:
    path = _path()
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for d in (data.get("days") if isinstance(data, dict) else None) or []:
        if not isinstance(d, dict):
            continue
        try:
            date.fromisoformat(str(d.get("date")))
        except ValueError:
            continue
        out.append({
            "id": str(d.get("id") or ""),
            "date": str(d["date"]),
            "title": str(d.get("title") or "")[:_TITLE_MAX],
            "yearly": bool(d.get("yearly", True)),
            "count": bool(d.get("count", False)),
        })
    out.sort(key=lambda d: d["date"])
    return out


def _save(days: list[dict]) -> None:
    path = _path()
    if not path:
        raise RuntimeError("buckets_dir 未配置")
    atomic_write_text(path, json.dumps({"days": days}, ensure_ascii=False, indent=1))


def add_day(when: str, title: str, *, yearly: bool = True, count: bool = False) -> dict:
    when = str(when or "").strip()
    try:
        parsed = date.fromisoformat(when)
    except ValueError as e:
        raise ValueError("date 要写成 YYYY-MM-DD，比如 2026-07-03") from e
    title = " ".join(str(title or "").split())[:_TITLE_MAX]
    if not title:
        raise ValueError("要给这个日子起个名字（title）")
    days = load_days()
    if count:
        for d in days:
            d["count"] = False
    for d in days:  # 同一天同一个名字就是同一个日子：改，不重复加
        if d["date"] == parsed.isoformat() and d["title"] == title:
            d["yearly"], d["count"] = bool(yearly), bool(count) or d["count"]
            _save(days)
            return d
    item = {
        "id": "d_" + secrets.token_hex(3),
        "date": parsed.isoformat(),
        "title": title,
        "yearly": bool(yearly),
        "count": bool(count),
    }
    days.append(item)
    days.sort(key=lambda d: d["date"])
    _save(days)
    return item


def delete_day(day_id: str) -> bool:
    days = load_days()
    kept = [d for d in days if d["id"] != str(day_id)]
    if len(kept) == len(days):
        return False
    _save(kept)
    return True


def _next_occurrence(d: dict, today: date) -> date | None:
    origin = date.fromisoformat(d["date"])
    if not d["yearly"]:
        return origin if origin >= today else None
    for year in (today.year, today.year + 1):
        try:
            cand = origin.replace(year=year)
        except ValueError:  # 2 月 29 日：平年按 2 月 28 日过
            cand = date(year, 2, 28)
        if cand >= today and cand >= origin:
            return cand
    return None


def _md(d: date) -> str:
    return f"{d.month}.{d.day}"


def _years(d: dict, on: date) -> int:
    return on.year - date.fromisoformat(d["date"]).year


def days_section(today: date | None = None) -> str:
    """无参 breath 的「日子」段；一个日子都没有时返回 ""。"""
    days = load_days()
    if not days:
        return ""
    today = today or _today()
    lines = []
    counter = next((d for d in days if d["count"]), None)
    if counter:
        start = date.fromisoformat(counter["date"])
        n = (today - start).days + 1
        if n >= 1:
            lines.append(f"今天 {_md(today)}（周{_WEEKDAYS[today.weekday()]}），是我们在一起的第 {n} 天。")
            nxt = (n // 100 + 1) * 100
            left = nxt - n
            if n % 100 == 0:
                lines.append(f"🎉 今天正好是第 {n} 天。")
            elif left <= _AHEAD_DAYS:
                lines.append(f"还有 {left} 天是第 {nxt} 天（{_md(today + timedelta(days=left))}）。")

    upcoming = []
    for d in days:
        when = _next_occurrence(d, today)
        if when is None:
            continue
        left = (when - today).days
        if left > _AHEAD_DAYS:
            continue
        nth = _years(d, when) if d["yearly"] else 0
        suffix = f"（第 {nth} 年）" if nth >= 1 else ""
        if left == 0:
            if nth == 0 and d["yearly"] and d["count"]:
                continue  # 起点那天本身不用提醒
            upcoming.append((0, f"🎂 今天就是：{d['title']}{suffix}"))
        else:
            upcoming.append((left, f"还有 {left} 天：{_md(when)} {d['title']}{suffix}"))
    upcoming.sort(key=lambda x: x[0])
    lines += [text for _, text in upcoming]
    if not lines:
        return ""
    return "=== 日子 ===\n" + "\n".join(lines)


def _list_text() -> str:
    days = load_days()
    if not days:
        return (
            "还没有记下任何日子。用 days(date=\"YYYY-MM-DD\", title=\"...\") 加；"
            "在一起的那天加 count=True，breath 里就会数第几天。"
        )
    today = _today()
    rows = []
    for d in days:
        when = _next_occurrence(d, today)
        flags = []
        if d["count"]:
            flags.append("从这天数第几天")
        flags.append("每年" if d["yearly"] else "只这一天")
        nxt = f"，下一次 {when.isoformat()}（还有 {(when - today).days} 天）" if when else "，已经过了"
        rows.append(f"[id={d['id']}] {d['date']} {d['title']}（{'，'.join(flags)}{nxt}）")
    section = days_section(today)
    return ("\n".join(rows) + ("\n\n" + section if section else "")).strip()


async def dispatch(
    date: str = "",
    title: str = "",
    yearly: bool = True,
    count: bool = False,
    delete_id: str = "",
) -> str:
    if delete_id:
        if delete_day(delete_id):
            return f"删掉了 {delete_id}。\n\n" + _list_text()
        return f"没有 id 是 {delete_id} 的日子。\n\n" + _list_text()
    if date or title:
        try:
            item = add_day(date, title, yearly=bool(yearly), count=bool(count))
        except ValueError as e:
            raise ToolInputError(str(e)) from e
        return f"记下了：{item['date']} {item['title']}（id={item['id']}）。\n\n" + _list_text()
    return _list_text()
