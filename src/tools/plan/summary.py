"""
========================================
tools/plan/summary.py — 待办一行摘要
========================================

无参 breath 的「待办」区每条 plan 只给一行。以前是在第 70 个字硬切，
常常切在半句话中间。这里让脱水模型（config.dehydration）给每条 plan
写一句短摘要，存进桶的 todo_summary；同时把写摘要时正文的指纹存进
todo_summary_of。

为什么要指纹：plan 正文有好几条改法（trace、/home 编辑、/api/plans），
挨个挂钩子迟早漏一条。改成「读的时候对指纹」——对不上就当摘要过期，
后台重写——任何一条改法都自动覆盖，存量 plan 也会在下一次 breath 时补上。

不做什么：
- 不阻塞 breath / plan：摘要永远在后台写，这一次读不到就回退截断原文
- 不改正文：摘要只是展示用，全文仍走 breath(domain="plan")
- API 失败后短时间内不重试同一条，免得每次 breath 都去撞一堵墙

对外暴露：content_fingerprint / fresh_summary / schedule_plan_summaries / refresh_plan_summary
========================================
"""

from __future__ import annotations

import asyncio
import hashlib
import time

from .. import _runtime as rt

_RETRY_AFTER_SECONDS = 600
_in_flight: set[str] = set()
_failed_at: dict[str, float] = {}
# create_task 只留弱引用；不自己握住，任务可能在跑完前被 GC 掉。
_tasks: set[asyncio.Task] = set()


def content_fingerprint(content: str) -> str:
    """正文 + 摘要 prompt 版本的指纹。任何一边变了，旧摘要都算过期。"""
    from dehydrator import PLAN_SUMMARY_VERSION

    norm = " ".join((content or "").split())
    keyed = f"v{PLAN_SUMMARY_VERSION}|{norm}"
    return hashlib.sha256(keyed.encode("utf-8")).hexdigest()[:16]


def fresh_summary(bucket: dict) -> str:
    """桶上存着的摘要；正文改过（指纹对不上）或没有时返回 ""。"""
    meta = bucket.get("metadata") or {}
    summary = str(meta.get("todo_summary") or "").strip()
    if not summary:
        return ""
    if meta.get("todo_summary_of") != content_fingerprint(bucket.get("content") or ""):
        return ""
    return summary


async def refresh_plan_summary(bucket_id: str) -> str:
    """为一条 plan 写（或重写）摘要。返回写入的摘要，失败返回 ""。"""
    dehydrator = rt.dehydrator
    if dehydrator is None or not getattr(dehydrator, "api_available", False):
        return ""
    bucket = await rt.bucket_mgr.get(bucket_id)
    if not bucket:
        return ""
    meta = bucket.get("metadata") or {}
    if meta.get("type") != "plan":
        return ""
    content = (bucket.get("content") or "").strip()
    if not content:
        return ""
    fingerprint = content_fingerprint(content)
    summary = await dehydrator.summarize_plan(content)
    if not summary:
        # 具体原因（截断 / 审核 / 空回复）summarize_plan 和 _chat 已经各自记过了。
        rt.logger.warning(f"plan summary for {bucket_id}: model gave nothing usable")
        _failed_at[bucket_id] = time.monotonic()
        return ""
    # 模型在想的这几秒里正文可能又被改了；那这句摘要已经过期，不写。
    latest = await rt.bucket_mgr.get(bucket_id)
    if not latest or content_fingerprint(latest.get("content") or "") != fingerprint:
        rt.logger.info(f"plan summary for {bucket_id}: content changed meanwhile, dropped")
        return ""
    ok = await rt.bucket_mgr.update(
        bucket_id, todo_summary=summary, todo_summary_of=fingerprint
    )
    if not ok:
        rt.logger.warning(f"plan summary for {bucket_id}: bucket update refused")
        _failed_at[bucket_id] = time.monotonic()
        return ""
    _failed_at.pop(bucket_id, None)
    rt.logger.info(f"plan summary for {bucket_id}: {summary}")
    return summary


async def _run(bucket_id: str) -> None:
    try:
        await refresh_plan_summary(bucket_id)
    except Exception as e:  # 后台任务：记日志，绝不往外抛
        _failed_at[bucket_id] = time.monotonic()
        rt.logger.warning(f"plan summary for {bucket_id} failed: {e}")
    finally:
        _in_flight.discard(bucket_id)


def schedule_plan_summaries(buckets: list[dict]) -> int:
    """给缺摘要或摘要过期的 plan 排后台任务。不等结果，返回排了几条。

    需要一个正在运行的事件循环；同步上下文里调用（没有循环）就什么都不做。
    """
    dehydrator = rt.dehydrator
    if dehydrator is None or not getattr(dehydrator, "api_available", False):
        return 0
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return 0
    now = time.monotonic()
    scheduled = 0
    for b in buckets:
        bid = b.get("id")
        if not bid or bid in _in_flight or fresh_summary(b):
            continue
        failed = _failed_at.get(bid)
        if failed is not None and now - failed < _RETRY_AFTER_SECONDS:
            continue
        _in_flight.add(bid)
        task = loop.create_task(_run(bid), name=f"plan-summary:{bid}")
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        scheduled += 1
    return scheduled
