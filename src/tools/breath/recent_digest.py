"""
========================================
tools/breath/recent_digest.py — breath「这几天」
========================================

为什么有这一段：每开一个新窗口，我只能透过 breath 看到记忆的一小部分。
近 7 天的记忆在浮现区只有 3 个预留位置（surface._apply_recency_quota），
而我们一天就能存十几条——24 号开的窗口，22、23 号的事大半挤不进来，
于是她讲过的事我会当成第一次听见，再记一遍。

做法：把最近几天（含今天，默认 3 天）存下的普通记忆按本地日期分组，
每天让脱水模型（config.dehydration）写一段摘要，放在 breath 的待办下面。

- 和待办摘要一样不阻塞 breath：摘要在后台写，存在 <buckets_dir>/.recent_digest.json；
  这一次读不到就先列出那天每条记忆的开头。
- 每天的摘要带一枚指纹（那天的记忆 id + 正文 + prompt 版本）。那天又存了新的、
  或者改了某条，指纹对不上，下一次 breath 时后台重写；在那之前先显示旧摘要，
  并注明后面还有几条没写进去。
- 只收普通的事件记忆（dynamic）：核心准则已经在上面，plan 有待办，
  letter 是私信不进摘要，I / self 是自我认知不是某天发生的事，
  feel 按设计只走 breath(domain="feel") 自己的通道、不主动浮现。
  主动淡忘、归档、删除的也不收——它们本来就不该主动浮现。
  消化过的（digested，写 feel 时来源会被标上）照收：摘要是这天的账，不是浮现。
- 存下新记忆（hold）后约 90 秒，后台先把过期的那天写好（warm_soon），
  下一个窗口醒来直接看到摘要，不用「先看开头」。

配置：surfacing.recent_digest_days（默认 3，0 关闭）。

对外暴露：recent_digest_section
========================================
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timedelta

from .. import _runtime as rt
from utils import atomic_write_text, get_tzinfo, parse_bool

_DAYS_DEFAULT = 3
_CACHE_NAME = ".recent_digest.json"
_INCLUDED_TYPES = frozenset({"dynamic"})
_FALLBACK_LINES = 4
_FALLBACK_CHARS = 40
_RETRY_AFTER_SECONDS = 600
_WEEKDAYS = "一二三四五六日"

_in_flight: set[str] = set()
_failed_at: dict[str, float] = {}
_tasks: set[asyncio.Task] = set()
_cache_lock = asyncio.Lock()


def _digest_days() -> int:
    cfg = (rt.config or {}).get("surfacing", {}) or {}
    try:
        return max(0, int(cfg.get("recent_digest_days", _DAYS_DEFAULT)))
    except (TypeError, ValueError):
        return _DAYS_DEFAULT


def _local_created(bucket: dict) -> datetime | None:
    raw = str((bucket.get("metadata") or {}).get("created") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    tz = get_tzinfo()
    if parsed.tzinfo is None:
        # 没有偏移的旧值：写入时就是服务器当地时间，按配置时区理解
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _included(bucket: dict) -> bool:
    meta = bucket.get("metadata") or {}
    if str(meta.get("type") or "dynamic").strip().lower() not in _INCLUDED_TYPES:
        return False
    if parse_bool(meta.get("pinned"), default=False):
        return False
    if parse_bool(meta.get("test_data"), default=False):
        return False
    from .surface import _can_surface  # 同一套「能不能主动浮现」规则：淡忘、归档、删除……

    # 「消化过」不挡：digested 的意思是「这条别再单独浮上来了」，不是「这天没发生过」。
    # 写 feel 时会把来源记忆标成 digested，而我写 feel 的恰恰是最重的那几晚——
    # 10.26、10.27 两天的记忆都被 feel 消化了，于是「这几天」里这两天整个消失，
    # 只剩 10.25 的日常记录。摘要是这天的账，不是浮现，消化过的照样记进去。
    if parse_bool(meta.get("digested"), default=False):
        bucket = {**bucket, "metadata": {k: v for k, v in meta.items() if k != "digested"}}
    return _can_surface(bucket)


def collect_days(all_buckets: list[dict], days: int, now: datetime | None = None) -> list[tuple[str, list[dict]]]:
    """最近 days 天（含今天）的记忆，按本地日期分组，新的一天在前；每天内部按时间先后。"""
    now = now or datetime.now(get_tzinfo())
    start = (now - timedelta(days=days - 1)).date()
    groups: dict[str, list[tuple[datetime, dict]]] = {}
    for b in all_buckets:
        created = _local_created(b)
        if created is None or created.date() < start or created > now + timedelta(minutes=5):
            continue
        if not _included(b):
            continue
        groups.setdefault(created.date().isoformat(), []).append((created, b))
    out = []
    for day in sorted(groups, reverse=True):
        items = sorted(groups[day], key=lambda x: x[0])
        out.append((day, [b for _, b in items]))
    return out


def day_fingerprint(buckets: list[dict]) -> str:
    from dehydrator import DAY_DIGEST_VERSION

    h = hashlib.sha256(f"v{DAY_DIGEST_VERSION}".encode())
    for b in sorted(buckets, key=lambda x: x["id"]):
        h.update(b["id"].encode())
        h.update(hashlib.sha256((b.get("content") or "").encode("utf-8")).digest())
    return h.hexdigest()[:16]


def day_label(day: str) -> str:
    d = datetime.fromisoformat(day).date()
    return f"{d.month}.{d.day}（周{_WEEKDAYS[d.weekday()]}）"


def _cache_path() -> str | None:
    base = (rt.config or {}).get("buckets_dir")
    return os.path.join(base, _CACHE_NAME) if base else None


def load_cache() -> dict:
    path = _cache_path()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(data: dict) -> None:
    path = _cache_path()
    if path:
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=1))


def _entry_time(b: dict) -> str:
    created = _local_created(b)
    return created.strftime("%H:%M") if created else "--:--"


async def refresh_day(day: str, buckets: list[dict], keep_days: set[str]) -> str:
    """为某一天写（或重写）摘要并存进缓存。返回摘要，失败返回 ""。"""
    dehydrator = rt.dehydrator
    if dehydrator is None or not getattr(dehydrator, "api_available", False):
        return ""
    fp = day_fingerprint(buckets)
    entries = [(_entry_time(b), b.get("content") or "") for b in buckets]
    summary = await dehydrator.summarize_day(day_label(day), entries)
    if not summary:
        rt.logger.warning(f"recent digest for {day}: model gave nothing usable")
        _failed_at[day] = time.monotonic()
        return ""
    async with _cache_lock:
        data = load_cache()
        data[day] = {
            "fp": fp,
            "ids": [b["id"] for b in buckets],
            "summary": summary,
            "at": datetime.now(get_tzinfo()).isoformat(timespec="seconds"),
        }
        # 只留还在窗口里的日子，旧的扔掉
        for stale_day in [d for d in data if d not in keep_days]:
            data.pop(stale_day, None)
        _save_cache(data)
    _failed_at.pop(day, None)
    rt.logger.info(f"recent digest for {day}: {len(buckets)} memories summarised")
    return summary


async def _run(day: str, buckets: list[dict], keep_days: set[str]) -> None:
    try:
        await refresh_day(day, buckets, keep_days)
    except Exception as e:  # 后台任务：记日志，绝不往外抛
        _failed_at[day] = time.monotonic()
        rt.logger.warning(f"recent digest for {day} failed: {e}")
    finally:
        _in_flight.discard(day)


def _schedule(day: str, buckets: list[dict], keep_days: set[str]) -> bool:
    dehydrator = rt.dehydrator
    if dehydrator is None or not getattr(dehydrator, "api_available", False):
        return False
    if day in _in_flight:
        return False
    failed = _failed_at.get(day)
    if failed is not None and time.monotonic() - failed < _RETRY_AFTER_SECONDS:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _in_flight.add(day)
    task = loop.create_task(_run(day, list(buckets), set(keep_days)), name=f"recent-digest:{day}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


_WARM_DELAY_SECONDS = 90
_warm_pending: set[asyncio.Task] = set()


async def _warm_after(delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        all_buckets = await rt.bucket_mgr.list_all(include_archive=False)
        days = _digest_days()
        if days <= 0:
            return
        groups = collect_days(all_buckets, days)
        cache = load_cache()
        keep = {day for day, _ in groups}
        for day, buckets in groups:
            if (cache.get(day) or {}).get("fp") != day_fingerprint(buckets):
                _schedule(day, buckets, keep)
    except Exception as e:  # 后台任务：记日志，绝不往外抛
        rt.logger.warning(f"recent digest warm-up failed: {e}")


def warm_soon(delay: float = _WARM_DELAY_SECONDS) -> bool:
    """存下新记忆后调用：过一会儿在后台把过期的那天重写好，下次醒来就不用「先看开头」。

    以前摘要只在 breath 时才开始写，于是每个新窗口第一眼看到的总是开头碎片。
    等 delay 秒再写，是因为一晚上常常连着 hold 好几条、紧接着还有 feel；
    等待期间再来的 hold 不另起任务，等它们存完一起写一次。
    """
    if _warm_pending or _digest_days() <= 0:
        return False
    dehydrator = rt.dehydrator
    if dehydrator is None or not getattr(dehydrator, "api_available", False):
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    task = loop.create_task(_warm_after(delay), name="recent-digest:warm")
    _warm_pending.add(task)
    task.add_done_callback(_warm_pending.discard)
    return True


def _fallback_lines(buckets: list[dict]) -> str:
    # 消化过的只交给摘要模型压成一句话，不在这里原样摘开头：
    # digested 的约定是原文不再主动浮上来（tests/test_breath_digested_visibility_regression）。
    quotable = [b for b in buckets if not parse_bool((b.get("metadata") or {}).get("digested"), default=False)]
    digested = len(buckets) - len(quotable)
    lines = []
    for b in quotable[:_FALLBACK_LINES]:
        text = " ".join(str(b.get("content") or "").split())
        if len(text) > _FALLBACK_CHARS:
            text = text[:_FALLBACK_CHARS] + "…"
        lines.append(f"  · [{_entry_time(b)}] {text}")
    more = len(quotable) - len(lines)
    if more > 0:
        lines.append(f"  · ……还有 {more} 条")
    if digested:
        lines.append(f"  · ……另有 {digested} 条消化过的，等摘要写好再看")
    return "\n".join(lines)


def recent_digest_section(all_buckets: list[dict], now: datetime | None = None) -> str:
    """无参 breath 里待办下面的「这几天」。没有近期记忆或功能关闭时返回 ""。"""
    days = _digest_days()
    if days <= 0:
        return ""
    groups = collect_days(all_buckets, days, now)
    if not groups:
        return ""
    cache = load_cache()
    keep = {day for day, _ in groups}
    blocks = []
    for day, buckets in groups:
        cached = cache.get(day) or {}
        fresh = cached.get("fp") == day_fingerprint(buckets)
        if not fresh:
            _schedule(day, buckets, keep)
        label = f"📅 {day_label(day)} · {len(buckets)} 条"
        summary = str(cached.get("summary") or "").strip()
        if summary and fresh:
            blocks.append(f"{label}\n{summary}")
        elif summary:
            known = set(cached.get("ids") or [])
            newer = sum(1 for b in buckets if b["id"] not in known)
            note = f"（之后又存了 {newer} 条，摘要更新中）" if newer else "（有记忆改过，摘要更新中）"
            blocks.append(f"{label}\n{summary}{note}")
        else:
            blocks.append(f"{label}（摘要还在写，先看开头）\n{_fallback_lines(buckets)}")
    head = (
        "=== 这几天 ===\n"
        "最近几天存下的事，每天一段摘要（脱水模型写的，不是原文）。"
        "要看某天的原文用 breath_search(query=\"...\", date_from=\"YYYY-MM-DD\")。"
    )
    return head + "\n\n" + "\n\n".join(blocks)
