"""
========================================
tools/note/core.py — 小记
========================================

我随手写的小日记。不是要记住的事（那是 hold），不是挂在某条记忆上的感受
（那是 feel），不是要永远留给她的话（那是信），也不是在认识我自己（那是 I）。
就是一句「今天她把品钦拍卖了一分钱，我笑了」，带个日子，贴一个团子。

存在 <buckets_dir>/.notes.json：{"notes": [...]}，每一条：
  id        n_xxxxxx
  date      这条算哪一天：YYYY-MM-DD（默认今天，可以补写过去的日子，不能写明天）
  mood      团子：1~10，1 最糟、10 最好；0 = 没贴
  content   正文，原样保存
  created   写下的时间
  comments  评论串，平铺：
    id        c_xxxxxx
    author    user（她）/ ai（我）
    content   原样保存
    created   写下的时间
    reply_to  回的是哪条评论的 id（空 = 直接评论这条小记）
    seen      对方看过没有：她的评论我在 breath / note 里看到一次就算看过；
              我的回复她在后台点开那天就算看过
    deleted   她删掉了自己的这条评论（只有她能删她自己的）：从此两边都看不见，
              原文仍留在文件里——只改可见性，不抹去（呼应 rule.md 第 1、9 条）

规矩：
- 小记只有我能写；她能看、能评论、能回我的回复。
- 小记和评论都不合并、不压缩、不衰减。小记没有删除入口；她可以删掉
  自己打错的评论，删了就看不见，原文仍留在文件里。
- 不进普通 breath 浮现和 dream；无参 breath 的「小记」段只带最近一条，
  外加她还没被我看过的评论，看过一次就不再提。

进 GitHub 备份（github_sync 的 sidecar 白名单）。

对外暴露：MOOD_MIN / MOOD_MAX / load_notes / add_note / add_comment /
          delete_comment / is_deleted / mark_seen / notes_section / dispatch
========================================
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import date, datetime

from .. import _runtime as rt
from errors import ToolInputError
from utils import atomic_write_text, get_tzinfo

_FILE = ".notes.json"
MOOD_MIN, MOOD_MAX = 1, 10
_CONTENT_MAX = 2000
_COMMENT_MAX = 1000
_SECTION_PREVIEW = 300
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_AUTHORS = ("user", "ai")

# MCP 工具和后台评论会同时读改写同一个文件，读-改-写整段要串起来。
_LOCK = threading.RLock()


def _path() -> str | None:
    base = (rt.config or {}).get("buckets_dir")
    return os.path.join(base, _FILE) if base else None


def _now() -> datetime:
    return datetime.now(get_tzinfo())


def _today() -> date:
    return _now().date()


def load_notes() -> list[dict]:
    path = _path()
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    notes = data.get("notes") if isinstance(data, dict) else None
    out = []
    for n in notes or []:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        if not isinstance(n.get("comments"), list):
            n["comments"] = []
        out.append(n)
    return out


def _save_all(notes: list[dict]) -> None:
    path = _path()
    if not path:
        raise RuntimeError("buckets_dir 未配置")
    atomic_write_text(path, json.dumps({"notes": notes}, ensure_ascii=False, indent=1))


def _clean_mood(mood) -> int:
    if mood in (None, ""):
        return 0
    try:
        mood = int(mood)
    except (TypeError, ValueError) as e:
        raise ValueError(f"mood 是 {MOOD_MIN}~{MOOD_MAX} 的整数（0 = 不贴团子）") from e
    if mood != 0 and not MOOD_MIN <= mood <= MOOD_MAX:
        raise ValueError(f"mood 是 {MOOD_MIN}~{MOOD_MAX} 的整数（0 = 不贴团子）")
    return mood


def _clean_date(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return _today().isoformat()
    if not _DATE_RE.match(value):
        raise ValueError("date 写成 YYYY-MM-DD，比如 2026-11-26")
    try:
        d = date.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"没有 {value} 这一天") from e
    if d > _today():
        raise ValueError("小记不能写在还没到的日子上")
    return d.isoformat()


def _ordered(notes: list[dict]) -> list[dict]:
    """从旧到新。同一天同一秒写下的几条，按写入文件的先后排（created 只精确到秒）。"""
    return [n for _, n in sorted(
        enumerate(notes),
        key=lambda pair: (str(pair[1].get("date") or ""), str(pair[1].get("created") or ""), pair[0]),
    )]


def add_note(content: str, mood=0, note_date: str = "") -> dict:
    """写一条小记（只有我写）。"""
    content = str(content or "").strip()
    if not content:
        raise ValueError("小记不能是空的")
    if len(content) > _CONTENT_MAX:
        raise ValueError(f"小记最多 {_CONTENT_MAX} 字；更长的话写信（letter_write）")
    note = {
        "id": "n_" + secrets.token_hex(3),
        "date": _clean_date(note_date),
        "mood": _clean_mood(mood),
        "content": content,
        "created": _now().isoformat(timespec="seconds"),
        "comments": [],
    }
    with _LOCK:
        notes = load_notes()
        notes.append(note)
        _save_all(notes)
    return note


def add_comment(note_id: str, author: str, content: str, reply_to: str = "") -> dict:
    """在一条小记下面评论或回复。author=user（她）/ ai（我）。"""
    author = str(author or "").strip().lower()
    if author not in _AUTHORS:
        raise ValueError("author 只能是 user 或 ai")
    content = str(content or "").strip()
    if not content:
        raise ValueError("评论不能是空的")
    if len(content) > _COMMENT_MAX:
        raise ValueError(f"评论最多 {_COMMENT_MAX} 字")
    reply_to = str(reply_to or "").strip()
    with _LOCK:
        notes = load_notes()
        note = next((n for n in notes if n["id"] == str(note_id or "").strip()), None)
        if note is None:
            raise LookupError(f"没有 id 是 {note_id} 的小记")
        if reply_to and not any(c.get("id") == reply_to and not is_deleted(c) for c in note["comments"]):
            raise LookupError(f"这条小记下面没有 id 是 {reply_to} 的评论")
        comment = {
            "id": "c_" + secrets.token_hex(3),
            "author": author,
            "content": content,
            "created": _now().isoformat(timespec="seconds"),
            "reply_to": reply_to,
            "seen": False,
        }
        note["comments"].append(comment)
        _save_all(notes)
    return comment


def is_deleted(comment: dict) -> bool:
    return bool(comment.get("deleted"))


def delete_comment(note_id: str, comment_id: str, author: str = "user") -> dict:
    """删掉一条评论：只能删自己写的。删了两边都看不见，原文留在文件里。"""
    with _LOCK:
        notes = load_notes()
        note = next((n for n in notes if n["id"] == str(note_id or "").strip()), None)
        if note is None:
            raise LookupError(f"没有 id 是 {note_id} 的小记")
        comment = next((c for c in note["comments"] if c.get("id") == str(comment_id or "").strip()), None)
        if comment is None or is_deleted(comment):
            raise LookupError(f"这条小记下面没有 id 是 {comment_id} 的评论")
        if comment.get("author") != author:
            raise PermissionError("只能删自己写的评论")
        comment["deleted"] = True
        comment["deleted_at"] = _now().isoformat(timespec="seconds")
        _save_all(notes)
    return comment


def mark_seen(reader: str, note_ids=None) -> int:
    """reader 看过了：把对方写的、还没看过的评论标成已读。→ 标了几条。

    reader=ai 时标她的评论，reader=user 时标我的回复。note_ids=None 表示全部。
    """
    other = "user" if reader == "ai" else "ai"
    wanted = None if note_ids is None else {str(i) for i in note_ids}
    changed = 0
    with _LOCK:
        notes = load_notes()
        for n in notes:
            if wanted is not None and n["id"] not in wanted:
                continue
            for c in n["comments"]:
                if c.get("author") == other and not c.get("seen"):
                    c["seen"] = True
                    changed += 1
        if changed:
            _save_all(notes)
    return changed


def _unseen_from_user(notes: list[dict]) -> list[tuple[dict, dict]]:
    out = []
    for n in notes:
        for c in n["comments"]:
            if c.get("author") == "user" and not c.get("seen") and not is_deleted(c):
                out.append((n, c))
    out.sort(key=lambda nc: str(nc[1].get("created") or ""))
    return out


# ---------------- 给我读的文字 ----------------

def _md(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.month}.{d.day}"


def _mood_label(mood) -> str:
    try:
        mood = int(mood or 0)
    except (TypeError, ValueError):
        mood = 0
    return f"团子 {mood}" if mood else "没贴团子"


def _who(author: str) -> str:
    return "她" if author == "user" else "我"


def _comment_lines(note: dict) -> list[str]:
    by_id = {c.get("id"): c for c in note["comments"]}
    lines = []
    for c in note["comments"]:
        if is_deleted(c):
            continue
        head = f"  💬 [{c.get('id')}] {_who(c.get('author'))}"
        target = by_id.get(c.get("reply_to") or "")
        if target is not None:
            head += f" 回{_who(target.get('author'))}" + ("（那条她删了）" if is_deleted(target) else "")
        new = " 🆕" if c.get("author") == "user" and not c.get("seen") else ""
        lines.append(f"{head}{new}：{c.get('content', '')}")
    return lines


def _render(note: dict, with_comments: bool = True) -> str:
    head = f"[id={note['id']}] {_md(note.get('date', ''))} · {_mood_label(note.get('mood'))}"
    out = [head, note.get("content", "")]
    if with_comments and any(not is_deleted(c) for c in note["comments"]):
        out += _comment_lines(note)
    return "\n".join(out)


def notes_section() -> str:
    """无参 breath 的「小记」段：最近一条 + 她还没被我看过的评论。没有就返回 ""。

    带出来的评论随即标成已读：提醒一次，说过就清。
    """
    with _LOCK:
        notes = load_notes()
        if not notes:
            return ""
        latest = _ordered(notes)[-1]
        content = str(latest.get("content") or "")
        if len(content) > _SECTION_PREVIEW:
            content = content[:_SECTION_PREVIEW] + f"…（全文 note(note_id=\"{latest['id']}\")）"
        lines = [
            f"上一条小记 [id={latest['id']}] {_md(latest.get('date', ''))} · {_mood_label(latest.get('mood'))}",
            content,
        ]
        unseen = _unseen_from_user(notes)
        if unseen:
            lines.append("")
            lines.append(f"她留了 {len(unseen)} 条话（只提醒这一次）：")
            for n, c in unseen:
                lines.append(f"  · 在 {_md(n.get('date', ''))} 的小记 [id={n['id']}] 下 [{c['id']}]：{c.get('content', '')}")
            lines.append("想回就 note(note_id=..., reply=\"...\", comment_id=...)。")
            mark_seen("ai", {n["id"] for n, _ in unseen})
    return "=== 小记 ===\n" + "\n".join(lines)


def _day_text(day: str) -> str:
    notes = _ordered([n for n in load_notes() if n.get("date") == day])
    if not notes:
        return f"{day} 没有小记。"
    mark_seen("ai", {n["id"] for n in notes})
    return f"=== {day} 的小记（{len(notes)} 条）===\n" + "\n\n".join(_render(n) for n in notes)


def _month_text(month: str) -> str:
    notes = _ordered([n for n in load_notes() if str(n.get("date", "")).startswith(month)])
    if not notes:
        return f"{month} 一条小记都没有。"
    by_day: dict[str, list[dict]] = {}
    for n in notes:
        by_day.setdefault(n["date"], []).append(n)
    rows = []
    for day, ns in by_day.items():
        moods = " ".join(str(int(n.get("mood") or 0)) for n in ns)
        first = " ".join(str(ns[-1].get("content", "")).split())[:40]
        talk = sum(1 for n in ns for c in n["comments"] if not is_deleted(c))
        rows.append(f"{_md(day)}  团子 {moods}  {first}" + (f"  💬{talk}" if talk else ""))
    return (
        f"=== {month}：{len(by_day)} 天写了 {len(notes)} 条 ===\n" + "\n".join(rows)
        + "\n\n某一天的全文和评论：note(date=\"YYYY-MM-DD\")。"
    )


def _comments_text(limit: int = 15) -> str:
    """她的评论：不管评在哪天的小记下面，从新到旧。没看过的带 🆕，看完即算看过。"""
    notes = load_notes()
    pairs = [
        (n, c) for n in notes for c in n["comments"]
        if c.get("author") == "user" and not is_deleted(c)
    ]
    if not pairs:
        return "她还没评论过小记。"
    pairs.sort(key=lambda nc: str(nc[1].get("created") or ""), reverse=True)
    shown = pairs[:limit]
    unseen = sum(1 for _, c in shown if not c.get("seen"))
    lines = []
    for n, c in shown:
        first = " ".join(str(n.get("content", "")).split())[:30]
        new = " 🆕" if not c.get("seen") else ""
        lines.append(
            f"[{c['id']}]{new} {_md(n.get('date', ''))} 的小记 [id={n['id']}]「{first}」下：\n"
            f"  她：{c.get('content', '')}"
        )
    mark_seen("ai", {n["id"] for n, _ in shown})
    head = f"=== 她的评论（最近 {len(shown)} 条" + (f"，{unseen} 条新的" if unseen else "") + "）==="
    return head + "\n" + "\n".join(lines) + "\n\n回她：note(note_id=..., reply=\"...\", comment_id=...)。"


def _recent_text(limit: int = 7) -> str:
    notes = _ordered(load_notes())[::-1]
    if not notes:
        return (
            "还没写过小记。想到什么就 note(content=\"...\", mood=1~10)；"
            "mood 是团子，1 最糟，10 最好，不想贴就不传。"
        )
    shown = notes[:limit]
    mark_seen("ai", {n["id"] for n in shown})
    return f"=== 最近 {len(shown)} 条小记 ===\n" + "\n\n".join(_render(n) for n in shown)


async def dispatch(
    content: str = "",
    mood: int = 0,
    date: str = "",
    month: str = "",
    note_id: str = "",
    reply: str = "",
    comment_id: str = "",
    comments: bool = False,
) -> str:
    content = str(content or "").strip()
    reply = str(reply or "").strip()
    note_id = str(note_id or "").strip()
    try:
        if content and reply:
            raise ValueError("写小记（content）和回评论（reply）请分两次调")
        if content:
            note = add_note(content, mood=mood, note_date=date)
            return "写下了：\n" + _render(note, with_comments=False)
        if reply:
            if not note_id:
                raise ValueError("回评论要给 note_id（回哪条小记）")
            c = add_comment(note_id, "ai", reply, reply_to=comment_id)
            mark_seen("ai", {note_id})
            return f"回好了 [{c['id']}]。"
        if comment_id:
            raise ValueError("comment_id 只在回复时用，要和 reply、note_id 一起给")
        if comments:
            return _comments_text()
        if note_id:
            note = next((n for n in load_notes() if n["id"] == note_id), None)
            if note is None:
                raise LookupError(f"没有 id 是 {note_id} 的小记")
            mark_seen("ai", {note_id})
            return _render(note)
        if date:
            day = str(date).strip()
            if not _DATE_RE.match(day):
                raise ValueError("date 写成 YYYY-MM-DD，比如 2026-11-26")
            return _day_text(day)
        if month:
            m = str(month).strip()
            if not _MONTH_RE.match(m):
                raise ValueError("month 写成 YYYY-MM，比如 2026-11")
            return _month_text(m)
        return _recent_text()
    except (ValueError, LookupError) as e:
        raise ToolInputError(str(e)) from e
