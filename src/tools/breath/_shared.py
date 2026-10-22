"""breath 五条分支共用的小工具。

`footprint_reader` 原本以「取 snapshot + 定义闭包 + 兜底文案」的形态在
surface / feel / catalog / importance / search 里各抄了一份（surface 抄了两份），
`bucket_has_tags` 抄了三份。抄的那几份迟早会不一样。
"""

from __future__ import annotations

from .. import _runtime as rt

_UNAVAILABLE = "👣 Footprint：暂时无法读取"


class FootprintReader:
    """一次快照，两种读法：渲染用的 summary，和归档判型用的 original_kind。

    可调用，所以旧的 `_footprint(bucket)` 写法原样能用。
    """

    __slots__ = ("snapshot",)

    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot

    def __call__(self, bucket: dict, meta: dict | None = None) -> str:
        if self.snapshot is None:
            return _UNAVAILABLE
        if meta is None:
            meta = bucket.get("metadata", {})
        return self.snapshot.summary(str(bucket.get("id") or ""), meta)

    def original_kind(self, bucket_id: str, meta: dict, default: str = "dynamic") -> str:
        if self.snapshot is None:
            return default
        return self.snapshot.original_kind(str(bucket_id or ""), meta)


def footprint_reader() -> FootprintReader:
    """取一次快照并包成读取器；拿不到时降级为固定兜底文案。

    快照只取一次：每条桶各取一次会把「省 token 的目录模式」变成 N 次全库读。
    """
    try:
        snapshot = rt.bucket_mgr.footprint_snapshot()
    except Exception as exc:
        rt.logger.warning(f"Footprint snapshot unavailable / 足迹读取失败: {exc}")
        snapshot = None
    return FootprintReader(snapshot)


def created_label(bucket: dict) -> str:
    """` [2026-10-22 03:14]`——这条记忆是哪天写下的；读不出来就返回空串。

    浮现时不带日期，读的人只能从正文里猜「这是哪天的事」，旧事很容易被当成
    新事讲。带偏移的时间换算到 config 的时区（搬到海外 VPS 后容器是 UTC，
    不换算的话凌晨的事会被记成前一天下午）；没有偏移的旧值按原样显示，因为它
    本来就是写入时服务器的当地时间。
    """
    raw = str((bucket.get("metadata") or {}).get("created") or "").strip()
    if not raw:
        return ""
    try:
        from datetime import datetime

        from utils import get_tzinfo

        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(get_tzinfo())
        return f" [{parsed:%Y-%m-%d %H:%M}]"
    except (TypeError, ValueError):
        return ""


def bucket_has_tags(meta: dict, tag_filter: list) -> bool:
    """tag 过滤是 AND：给了几个就必须全都有。"""
    if not tag_filter:
        return True
    bucket_tags = set(meta.get("tags", []) or [])
    return all(t in bucket_tags for t in tag_filter)


def render_within_budget(
    buckets: list[dict],
    max_tokens: int,
    footprint,
) -> tuple[list[str], int]:
    """按 `[created] [bucket_id:x]` 头逐条渲染，返回 (已渲染, 未返回条数)。

    放不下就**整条停下**，不截断也不摘要——feel 与 plan 两条通道都靠这个语义：
    截断过的正文比没返回更糟，因为看不出少了什么。

    feel.py 与 surface_plans 原本各写了一份逐字相同的循环（surface_plans 的
    docstring 甚至写着「与 feel 通道同构」）。
    """
    from ._verbatim import render_stored_bucket

    lines: list[str] = []
    used = 0
    for index, bucket in enumerate(buckets):
        # 与无参 breath 同一个格式、同一个时区：以前这里直接放 created 原值，
        # 搬到 UTC 容器后读出来是 2026-10-22T01:56:35+00:00，跟首页对不上。
        label = created_label(bucket).strip()
        if not label:
            raw = str(bucket["metadata"].get("created") or "").strip()
            label = f"[{raw}]" if raw else ""
        head = f"{label} [bucket_id:{bucket['id']}]" if label else f"[bucket_id:{bucket['id']}]"
        entry, cost = render_stored_bucket(bucket, head, footprint(bucket))
        if used + cost > max_tokens:
            return lines, len(buckets) - index
        lines.append(entry)
        used += cost
    return lines, 0
