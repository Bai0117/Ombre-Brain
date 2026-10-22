"""
========================================
tools/shelf/core.py — 书架 / 片单
========================================

存在 <buckets_dir>/.shelf.json：{"items": [...]}，每一项：
  id        s_xxxxxx
  kind      book / film / series / game
  title     书名或片名
  creator   作者 / 导演 / 工作室（可空）
  status    done（看完）/ reading（在看）/ want（想看）/ dropped（弃了）
  finished  看完的日子：YYYY-MM-DD 或 YYYY-MM（可空）
  rating    0~5，0 = 没打分
  tags      类型、气质：["侦探", "秋天", "纳博科夫"]
  note      她的一句话感受
  added     记下的时间

同一个 kind + title（不分大小写、去掉空白）就是同一项：再记一次是改，不是新加。

taste()：她的口味——今年看了多少、最常出现的类型和作者（带平均分）、
打了高分的、在看的、想看的。我推荐之前先看这个。

不进 GitHub 备份（那边只同步 .md）；后台「书架」页能看到全部。

对外暴露：KINDS / STATUSES / load_items / save_item / delete_item / taste / dispatch
========================================
"""

from __future__ import annotations

import json
import os
import re
import secrets
from collections import Counter, defaultdict
from datetime import date, datetime

from .. import _runtime as rt
from errors import ToolInputError
from utils import atomic_write_text, get_tzinfo

KINDS = {"book": "书", "film": "电影", "series": "剧", "game": "游戏"}
STATUSES = {"done": "看完了", "reading": "在看", "want": "想看", "dropped": "弃了"}
_FILE = ".shelf.json"
_TITLE_MAX = 120
_NOTE_MAX = 400
_TAG_MAX = 24
_TAGS_MAX = 12
_FINISHED_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _path() -> str | None:
    base = (rt.config or {}).get("buckets_dir")
    return os.path.join(base, _FILE) if base else None


def _norm_title(t: str) -> str:
    return re.sub(r"\s+", "", str(t or "")).lower()


def _clean_tags(raw) -> list[str]:
    if isinstance(raw, str):
        raw = re.split(r"[,，、/]", raw)
    out: list[str] = []
    for t in raw or []:
        t = " ".join(str(t).split())[:_TAG_MAX]
        if t and t not in out:
            out.append(t)
    return out[:_TAGS_MAX]


def load_items() -> list[dict]:
    path = _path()
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in (items or []) if isinstance(i, dict) and i.get("id") and i.get("title")]


def _save_all(items: list[dict]) -> None:
    path = _path()
    if not path:
        raise RuntimeError("buckets_dir 未配置")
    atomic_write_text(path, json.dumps({"items": items}, ensure_ascii=False, indent=1))


def save_item(
    *,
    title: str,
    kind: str | None = None,
    creator: str | None = None,
    status: str | None = None,
    finished: str | None = None,
    rating: int | None = None,
    tags=None,
    note: str | None = None,
    item_id: str = "",
) -> tuple[dict, bool]:
    """加一项或改一项。→ (那一项, 是不是新加的)。None 表示「这个字段不改」。"""
    title = " ".join(str(title or "").split())[:_TITLE_MAX]
    kind = str(kind).strip().lower() if kind else None
    if kind is not None and kind not in KINDS:
        raise ValueError(f"kind 只能是 {' / '.join(KINDS)}")
    if status is not None:
        status = str(status).strip().lower()
        if status and status not in STATUSES:
            raise ValueError(f"status 只能是 {' / '.join(STATUSES)}")
    if finished is not None:
        finished = str(finished).strip()
        if finished and not _FINISHED_RE.match(finished):
            raise ValueError("finished 写成 YYYY-MM-DD 或 YYYY-MM，比如 2026-10-14")
    if rating is not None:
        try:
            rating = int(rating)
        except (TypeError, ValueError) as e:
            raise ValueError("rating 是 0~5 的整数") from e
        if not 0 <= rating <= 5:
            raise ValueError("rating 是 0~5 的整数")

    items = load_items()
    target = None
    if item_id:
        target = next((i for i in items if i["id"] == item_id), None)
        if target is None:
            raise ValueError(f"没有 id 是 {item_id} 的条目")
    elif title:
        target = next(
            (i for i in items
             if i.get("kind") == (kind or "book") and _norm_title(i.get("title")) == _norm_title(title)),
            None,
        )
    if target is None and not title:
        raise ValueError("要写书名或片名（title）")

    created = target is None
    if created:
        target = {
            "id": "s_" + secrets.token_hex(3),
            "kind": kind or "book",
            "title": title,
            "creator": "",
            "status": "done",
            "finished": "",
            "rating": 0,
            "tags": [],
            "note": "",
            "added": datetime.now(get_tzinfo()).isoformat(timespec="seconds"),
        }
        items.append(target)
    else:
        if title:
            target["title"] = title
        if kind:
            target["kind"] = kind
    if creator is not None:
        target["creator"] = " ".join(str(creator).split())[:_TITLE_MAX]
    if status:
        target["status"] = status
    if finished is not None:
        target["finished"] = finished
    if rating is not None:
        target["rating"] = rating
    if tags is not None:
        target["tags"] = _clean_tags(tags)
    if note is not None:
        target["note"] = str(note).strip()[:_NOTE_MAX]
    # 写了看完的日子却没写状态：那就是看完了
    if created and target["finished"] and not status:
        target["status"] = "done"
    _save_all(items)
    return target, created


def delete_item(item_id: str) -> bool:
    items = load_items()
    kept = [i for i in items if i["id"] != str(item_id)]
    if len(kept) == len(items):
        return False
    _save_all(kept)
    return True


def _year_of(item: dict) -> int | None:
    f = str(item.get("finished") or "")
    return int(f[:4]) if len(f) >= 4 and f[:4].isdigit() else None


def _sort_key(item: dict):
    return (str(item.get("finished") or ""), str(item.get("added") or ""))


def taste(items: list[dict] | None = None, today: date | None = None) -> dict:
    """口味：给推荐看的。"""
    items = load_items() if items is None else items
    today = today or datetime.now(get_tzinfo()).date()
    done = [i for i in items if i.get("status") == "done"]
    this_year = [i for i in done if _year_of(i) == today.year]

    tag_ratings: dict[str, list[int]] = defaultdict(list)
    tag_count: Counter = Counter()
    creator_count: Counter = Counter()
    creator_ratings: dict[str, list[int]] = defaultdict(list)
    for i in done:
        r = int(i.get("rating") or 0)
        for t in i.get("tags") or []:
            tag_count[t] += 1
            if r:
                tag_ratings[t].append(r)
        c = str(i.get("creator") or "").strip()
        if c:
            creator_count[c] += 1
            if r:
                creator_ratings[c].append(r)

    def _avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    return {
        "year": today.year,
        "this_year": {k: sum(1 for i in this_year if i.get("kind") == k) for k in KINDS},
        "this_year_total": len(this_year),
        "top_tags": [{"tag": t, "count": n, "avg": _avg(tag_ratings[t])} for t, n in tag_count.most_common(10)],
        "top_creators": [
            {"creator": c, "count": n, "avg": _avg(creator_ratings[c])}
            for c, n in creator_count.most_common(8) if n >= 1
        ],
        "loved": sorted([i for i in done if int(i.get("rating") or 0) >= 4], key=_sort_key, reverse=True)[:12],
        "disliked": sorted([i for i in done if 0 < int(i.get("rating") or 0) <= 2], key=_sort_key, reverse=True)[:8],
        "reading": [i for i in items if i.get("status") == "reading"],
        "want": [i for i in items if i.get("status") == "want"],
        "recent": sorted(done, key=_sort_key, reverse=True)[:10],
    }


# ---------------- 给我读的文字 ----------------

def _line(i: dict) -> str:
    kind = KINDS.get(i.get("kind"), i.get("kind"))
    bits = [f"[id={i['id']}] {kind}《{i['title']}》"]
    if i.get("creator"):
        bits.append(i["creator"])
    if i.get("finished"):
        bits.append(i["finished"])
    if i.get("status") and i["status"] != "done":
        bits.append(STATUSES.get(i["status"], i["status"]))
    if int(i.get("rating") or 0):
        bits.append("★" * int(i["rating"]))
    if i.get("tags"):
        bits.append("#" + " #".join(i["tags"]))
    line = " · ".join(bits)
    if i.get("note"):
        line += f"\n    「{i['note']}」"
    return line


def _overview() -> str:
    items = load_items()
    if not items:
        return (
            "书架还是空的。她说起读完、看完、玩完什么，就用 shelf(title=..., kind=..., "
            "finished=..., rating=..., tags=..., note=...) 记下来。"
        )
    t = taste(items)
    out = []
    counts = "，".join(f"{KINDS[k]} {n}" for k, n in t["this_year"].items() if n)
    out.append(f"=== {t['year']} 年看完 {t['this_year_total']} 部 ===" + (f"（{counts}）" if counts else ""))
    if t["reading"]:
        out.append("在看：\n" + "\n".join(_line(i) for i in t["reading"]))
    if t["recent"]:
        out.append("最近看完：\n" + "\n".join(_line(i) for i in t["recent"]))
    if t["top_tags"]:
        out.append("常看的类型：" + "、".join(
            f"{x['tag']}×{x['count']}" + (f"（均 {x['avg']}★）" if x["avg"] else "") for x in t["top_tags"]))
    if t["top_creators"]:
        out.append("常看的人：" + "、".join(
            f"{x['creator']}×{x['count']}" + (f"（均 {x['avg']}★）" if x["avg"] else "") for x in t["top_creators"]))
    if t["loved"]:
        out.append("她打了高分的：" + "、".join(f"《{i['title']}》" for i in t["loved"]))
    if t["disliked"]:
        out.append("她不太喜欢的：" + "、".join(f"《{i['title']}》" for i in t["disliked"]))
    if t["want"]:
        out.append("想看：\n" + "\n".join(_line(i) for i in t["want"]))
    out.append("推荐之前看上面的口味；她看过的（全部用 shelf(year=...) 或 query=... 查）别再推。")
    return "\n\n".join(out)


def _listing(year: int = 0, query: str = "") -> str:
    items = load_items()
    if year:
        items = [i for i in items if _year_of(i) == year]
    if query:
        q = query.strip().lower()
        items = [
            i for i in items
            if q in str(i.get("title", "")).lower()
            or q in str(i.get("creator", "")).lower()
            or any(q in str(t).lower() for t in i.get("tags") or [])
            or q in str(i.get("note", "")).lower()
        ]
    if not items:
        return "没有找到。"
    items.sort(key=_sort_key, reverse=True)
    head = f"{year} 年" if year else ""
    head += f"「{query}」" if query else ""
    return f"=== {head}{len(items)} 部 ===\n" + "\n".join(_line(i) for i in items)


async def dispatch(
    title: str = "",
    kind: str = "",
    creator: str = "",
    status: str = "",
    finished: str = "",
    rating: int = -1,
    tags: str = "",
    note: str = "",
    item_id: str = "",
    delete_id: str = "",
    year: int = 0,
    query: str = "",
) -> str:
    if delete_id:
        if delete_item(delete_id):
            return f"从书架上拿掉了 {delete_id}。"
        raise ToolInputError(f"没有 id 是 {delete_id} 的条目。")
    if title or item_id:
        try:
            item, created = save_item(
                title=title,
                kind=kind or None,
                creator=creator or None,
                status=status or None,
                finished=finished or None,
                rating=None if rating is None or int(rating) < 0 else int(rating),
                tags=tags or None,
                note=note or None,
                item_id=item_id,
            )
        except ValueError as e:
            raise ToolInputError(str(e)) from e
        return ("放上书架了：" if created else "改好了：") + "\n" + _line(item)
    if year or query:
        return _listing(int(year or 0), query or "")
    return _overview()
