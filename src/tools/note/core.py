"""
========================================
tools/note/core.py — 小记
========================================

我随手写的小日记。不是要记住的事（那是 hold），不是挂在某条记忆上的感受
（那是 feel），不是要永远留给她的话（那是信），也不是在认识我自己（那是 I）。
就是一句「今天她把品钦拍卖了一分钱，我笑了」，带个日子，贴一个团子。

旁边还有一块她的：她在后台写她自己的小记、贴她自己的团子，我来看、来评论。
两块板放在同一个文件里，靠每条的 author 分开。

存在 <buckets_dir>/.notes.json：{"notes": [...]}，每一条：
  id        n_xxxxxx
  author    ai（我的板，缺省即是）/ user（她的板）
  date      这条算哪一天：YYYY-MM-DD（默认今天，可以补写过去的日子，不能写明天）
  mood      团子：1~10，1 最糟、10 最好；0 = 没贴
  tag       心情小标签：贴在大团子底下的一个词（最多 12 字），空着就显示那个团子的默认名字
  content   正文，原样保存
  created   写下的时间
  seen      （只她的小记有）我看过没有：breath 或 note 里带出来一次就算看过；她改过就重新算没看过
  edited    （只她的小记有）最后改的时间
  history   （只她的小记有）改之前的版本 [{content, mood, date, until}]——改了不抹旧的
  deleted   （只她的小记有）她删掉了：两边都看不见，原文留在文件里
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
- 我的板只有我能写（MCP 的 note 工具），写下不能改、不能删；她能看、能评论。
- 她的板只有她能写（后台），她能改能删自己的；我能看、能评论。
- 小记和评论都不合并、不压缩、不衰减。她删掉的小记和评论只改可见性，
  原文仍留在文件里；她改过的小记，旧版本留在 history 里。
- 不进普通 breath 浮现和 dream；无参 breath 的「小记」段带我最近一条、
  她还没被我看过的新小记、她还没被我看过的评论，都只提醒一次。

进 GitHub 备份（github_sync 的 sidecar 白名单）。

对外暴露：MOOD_MIN / MOOD_MAX / load_notes / note_author / add_note / edit_note /
          delete_note / add_comment / delete_comment / is_deleted / mark_seen /
          notes_section / dispatch
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
_TAG_MAX = 12
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


def note_author(note: dict) -> str:
    """这条在谁的板上。旧数据没有 author 字段，都是我的。"""
    return "user" if note.get("author") == "user" else "ai"


def _live(notes: list[dict], whose: str | None = None) -> list[dict]:
    """去掉她删掉的；whose 给了就只留那块板的。"""
    return [n for n in notes if not is_deleted(n) and (whose is None or note_author(n) == whose)]


def _clean_tag(tag) -> str:
    tag = " ".join(str(tag or "").split())
    if len(tag) > _TAG_MAX:
        raise ValueError(f"心情小标签最多 {_TAG_MAX} 个字")
    return tag


def _clean_content(content) -> str:
    content = str(content or "").strip()
    if not content:
        raise ValueError("小记不能是空的")
    if len(content) > _CONTENT_MAX:
        raise ValueError(f"小记最多 {_CONTENT_MAX} 字；更长的话写信")
    return content


def _ordered(notes: list[dict]) -> list[dict]:
    """从旧到新。同一天同一秒写下的几条，按写入文件的先后排（created 只精确到秒）。"""
    return [n for _, n in sorted(
        enumerate(notes),
        key=lambda pair: (str(pair[1].get("date") or ""), str(pair[1].get("created") or ""), pair[0]),
    )]


def add_note(content: str, mood=0, note_date: str = "", author: str = "ai", tag: str = "") -> dict:
    """写一条小记。author=ai 写在我的板上（MCP），author=user 写在她的板上（后台）。"""
    if author not in _AUTHORS:
        raise ValueError("author 只能是 user 或 ai")
    note = {
        "id": "n_" + secrets.token_hex(3),
        "author": author,
        "date": _clean_date(note_date),
        "mood": _clean_mood(mood),
        "tag": _clean_tag(tag),
        "content": _clean_content(content),
        "created": _now().isoformat(timespec="seconds"),
        "comments": [],
    }
    if author == "user":
        note["seen"] = False
    with _LOCK:
        notes = load_notes()
        notes.append(note)
        _save_all(notes)
    return note


def _find_own(notes: list[dict], note_id: str, author: str) -> dict:
    note = next((n for n in notes if n["id"] == str(note_id or "").strip()), None)
    if note is None or is_deleted(note):
        raise LookupError(f"没有 id 是 {note_id} 的小记")
    if note_author(note) != author:
        raise PermissionError("只能改、删自己板上的小记")
    return note


def edit_note(note_id: str, author: str = "user", content=None, mood=None, note_date=None, tag=None) -> dict:
    """她改自己的小记。改之前的版本存进 history，不抹掉；改完我要重新看一次。"""
    if author != "user":
        raise PermissionError("我的小记写下就不改")
    with _LOCK:
        notes = load_notes()
        note = _find_own(notes, note_id, author)
        new_content = note["content"] if content is None else _clean_content(content)
        new_mood = note.get("mood", 0) if mood is None else _clean_mood(mood)
        new_date = note["date"] if note_date in (None, "") else _clean_date(note_date)
        new_tag = note.get("tag", "") if tag is None else _clean_tag(tag)
        old = (note["content"], note.get("mood", 0), note["date"], note.get("tag", ""))
        if (new_content, new_mood, new_date, new_tag) == old:
            return note
        now = _now().isoformat(timespec="seconds")
        note.setdefault("history", []).append({
            "content": old[0], "mood": old[1], "date": old[2], "tag": old[3], "until": now,
        })
        note.update({"content": new_content, "mood": new_mood, "date": new_date, "tag": new_tag,
                     "edited": now, "seen": False})
        _save_all(notes)
    return note


def delete_note(note_id: str, author: str = "user") -> dict:
    """她删自己的小记：两边都看不见，原文和评论都留在文件里。"""
    if author != "user":
        raise PermissionError("我的小记写下就不删")
    with _LOCK:
        notes = load_notes()
        note = _find_own(notes, note_id, author)
        note["deleted"] = True
        note["deleted_at"] = _now().isoformat(timespec="seconds")
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
        if note is None or is_deleted(note):
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


def is_deleted(item: dict) -> bool:
    """评论或小记被她删掉了没有。"""
    return bool(item.get("deleted"))


def delete_comment(note_id: str, comment_id: str, author: str = "user") -> dict:
    """删掉一条评论：只能删自己写的。删了两边都看不见，原文留在文件里。"""
    with _LOCK:
        notes = load_notes()
        note = next((n for n in notes if n["id"] == str(note_id or "").strip()), None)
        if note is None or is_deleted(note):
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

    reader=ai 时标她的评论（以及她的小记本身），reader=user 时标我的回复。
    note_ids=None 表示全部。
    """
    other = "user" if reader == "ai" else "ai"
    wanted = None if note_ids is None else {str(i) for i in note_ids}
    changed = 0
    with _LOCK:
        notes = load_notes()
        for n in notes:
            if wanted is not None and n["id"] not in wanted:
                continue
            if reader == "ai" and note_author(n) == "user" and not n.get("seen", True):
                n["seen"] = True
                changed += 1
            for c in n["comments"]:
                if c.get("author") == other and not c.get("seen"):
                    c["seen"] = True
                    changed += 1
        if changed:
            _save_all(notes)
    return changed


def _unseen_from_user(notes: list[dict]) -> list[tuple[dict, dict]]:
    out = []
    for n in _live(notes):
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


def _mood_text(note: dict) -> str:
    """团子 8「抱住了」——有心情小标签就带上。"""
    tag = str(note.get("tag") or "").strip()
    return _mood_label(note.get("mood")) + (f"「{tag}」" if tag else "")


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


def _board(note: dict) -> str:
    return "她的小记" if note_author(note) == "user" else "小记"


def _render(note: dict, with_comments: bool = True) -> str:
    head = f"[id={note['id']}] {_md(note.get('date', ''))} · {_mood_text(note)}"
    if note_author(note) == "user":
        head = "她的 " + head + ("（改过）" if note.get("edited") else "")
    out = [head, note.get("content", "")]
    if with_comments and any(not is_deleted(c) for c in note["comments"]):
        out += _comment_lines(note)
    return "\n".join(out)


def notes_section() -> str:
    """无参 breath 的「小记」段：我最近一条、她还没被我看过的新小记、她还没被我看过的评论。

    带出来的都随即标成看过：提醒一次，说过就清。什么都没有就返回 ""。
    """
    with _LOCK:
        notes = load_notes()
        mine = _ordered(_live(notes, "ai"))
        hers_new = [n for n in _ordered(_live(notes, "user")) if not n.get("seen", True)]
        unseen = _unseen_from_user(notes)
        if not mine and not hers_new and not unseen:
            return ""
        lines = []
        if mine:
            latest = mine[-1]
            content = str(latest.get("content") or "")
            if len(content) > _SECTION_PREVIEW:
                content = content[:_SECTION_PREVIEW] + f"…（全文 note(note_id=\"{latest['id']}\")）"
            lines += [
                f"上一条小记 [id={latest['id']}] {_md(latest.get('date', ''))} · {_mood_text(latest)}",
                content,
            ]
        if hers_new:
            if lines:
                lines.append("")
            lines.append(f"她写了 {len(hers_new)} 条新小记（只提醒这一次）：")
            for n in hers_new[-3:]:
                content = str(n.get("content") or "")
                if len(content) > _SECTION_PREVIEW:
                    content = content[:_SECTION_PREVIEW] + f"…（全文 note(note_id=\"{n['id']}\")）"
                edited = "（改过）" if n.get("edited") else ""
                lines.append(f"  · [id={n['id']}] {_md(n.get('date', ''))} · {_mood_text(n)}{edited}")
                lines.append("    " + content.replace("\n", "\n    "))
            if len(hers_new) > 3:
                lines.append(f"  ……还有 {len(hers_new) - 3} 条，note(her=True) 看。")
            lines.append("想评论就 note(note_id=..., reply=\"...\")。")
        if unseen:
            if lines:
                lines.append("")
            lines.append(f"她留了 {len(unseen)} 条话（只提醒这一次）：")
            for n, c in unseen:
                lines.append(
                    f"  · 在 {_md(n.get('date', ''))} 的{_board(n)} [id={n['id']}] 下 [{c['id']}]：{c.get('content', '')}"
                )
            lines.append("想回就 note(note_id=..., reply=\"...\", comment_id=...)。")
        mark_seen("ai", {n["id"] for n in hers_new} | {n["id"] for n, _ in unseen})
    return "=== 小记 ===\n" + "\n".join(lines)


def _day_text(day: str, whose: str = "ai") -> str:
    board = "她的小记" if whose == "user" else "小记"
    notes = _ordered([n for n in _live(load_notes(), whose) if n.get("date") == day])
    if not notes:
        return f"{day} 没有{board}。"
    mark_seen("ai", {n["id"] for n in notes})
    return f"=== {day} 的{board}（{len(notes)} 条）===\n" + "\n\n".join(_render(n) for n in notes)


def _month_text(month: str, whose: str = "ai") -> str:
    board = "她的小记" if whose == "user" else "小记"
    notes = _ordered([n for n in _live(load_notes(), whose) if str(n.get("date", "")).startswith(month)])
    if not notes:
        return f"{month} 一条{board}都没有。"
    by_day: dict[str, list[dict]] = {}
    for n in notes:
        by_day.setdefault(n["date"], []).append(n)
    rows = []
    for day, ns in by_day.items():
        moods = " ".join(
            str(int(n.get("mood") or 0)) + (f"「{n['tag']}」" if n.get("tag") else "") for n in ns
        )
        first = " ".join(str(ns[-1].get("content", "")).split())[:40]
        talk = sum(1 for n in ns for c in n["comments"] if not is_deleted(c))
        rows.append(f"{_md(day)}  团子 {moods}  {first}" + (f"  💬{talk}" if talk else ""))
    return (
        f"=== {month} 的{board}：{len(by_day)} 天写了 {len(notes)} 条 ===\n" + "\n".join(rows)
        + "\n\n某一天的全文和评论：note(date=\"YYYY-MM-DD\"" + (", her=True" if whose == "user" else "") + ")。"
    )


def _comments_text(day: str) -> str:
    """那一天的小记底下，她还没被我看过的评论。看过的不再出来，来回聊多少轮都不重复。

    她回的是哪句，就带上那句的开头当提示；想看完整的来回用 note(date=...)。
    这一天没有新的、别的日子有，就说是哪几天，免得漏掉她在旧小记下面的话。
    """
    notes = _live(load_notes())
    day_notes = _ordered([n for n in notes if n.get("date") == day])
    blocks = []
    count = 0
    for n in day_notes:
        by_id = {c.get("id"): c for c in n["comments"]}
        fresh = [
            c for c in n["comments"]
            if c.get("author") == "user" and not c.get("seen") and not is_deleted(c)
        ]
        if not fresh:
            continue
        count += len(fresh)
        first = " ".join(str(n.get("content", "")).split())[:30]
        lines = [f"{_board(n)} [id={n['id']}]「{first}」"]
        for c in fresh:
            target = by_id.get(c.get("reply_to") or "")
            quote = ""
            if target is not None:
                gist = " ".join(str(target.get("content", "")).split())
                gist = gist[:24] + ("…" if len(gist) > 24 else "")
                quote = f" 回{_who(target.get('author'))}「{gist}」" if not is_deleted(target) else " 回她（那条她删了）"
            lines.append(f"  💬 [{c['id']}] 她{quote}：{c.get('content', '')}")
        blocks.append("\n".join(lines))
    if blocks:
        mark_seen("ai", {n["id"] for n in day_notes})
        out = f"=== {day} 的新评论（{count} 条）===\n" + "\n\n".join(blocks)
        out += "\n\n回她：note(note_id=..., reply=\"...\", comment_id=...)；完整来回：note(date=\"" + day + "\")。"
    else:
        out = f"{day} 没有新评论。"
    others: dict[str, int] = {}
    for n in notes:
        if n.get("date") == day:
            continue
        k = sum(1 for c in n["comments"]
                if c.get("author") == "user" and not c.get("seen") and not is_deleted(c))
        if k:
            others[n["date"]] = others.get(n["date"], 0) + k
    if others:
        days = "、".join(f"{_md(d)}（{k}）" for d, k in sorted(others.items()))
        out += f"\n别的日子还有新的：{days}，用 note(comments=True, date=\"YYYY-MM-DD\") 看。"
    return out


def _recent_text(limit: int = 7, whose: str = "ai") -> str:
    notes = _ordered(_live(load_notes(), whose))[::-1]
    if not notes:
        if whose == "user":
            return "她还没写过小记。"
        return (
            "还没写过小记。想到什么就 note(content=\"...\", mood=1~10)；"
            "mood 是团子，1 最糟，10 最好，不想贴就不传。"
        )
    shown = notes[:limit]
    mark_seen("ai", {n["id"] for n in shown})
    board = "她的小记" if whose == "user" else "小记"
    return f"=== 最近 {len(shown)} 条{board} ===\n" + "\n\n".join(_render(n) for n in shown)


async def dispatch(
    content: str = "",
    mood: int = 0,
    date: str = "",
    month: str = "",
    note_id: str = "",
    reply: str = "",
    comment_id: str = "",
    comments: bool = False,
    her: bool = False,
    tag: str = "",
) -> str:
    content = str(content or "").strip()
    whose = "user" if her else "ai"
    reply = str(reply or "").strip()
    note_id = str(note_id or "").strip()
    try:
        if content and reply:
            raise ValueError("写小记（content）和回评论（reply）请分两次调")
        if content and her:
            raise ValueError("她的小记只有她能写（后台小记页）；我能评论：note(note_id=..., reply=\"...\")")
        if tag and not content:
            raise ValueError("tag 是写小记时贴的心情小标签，要和 content 一起给")
        if content:
            note = add_note(content, mood=mood, note_date=date, tag=tag)
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
            day = str(date or "").strip() or _today().isoformat()
            if not _DATE_RE.match(day):
                raise ValueError("date 写成 YYYY-MM-DD，比如 2026-11-26")
            return _comments_text(day)
        if note_id:
            note = next((n for n in _live(load_notes()) if n["id"] == note_id), None)
            if note is None:
                raise LookupError(f"没有 id 是 {note_id} 的小记")
            mark_seen("ai", {note_id})
            return _render(note)
        if date:
            day = str(date).strip()
            if not _DATE_RE.match(day):
                raise ValueError("date 写成 YYYY-MM-DD，比如 2026-11-26")
            return _day_text(day, whose)
        if month:
            m = str(month).strip()
            if not _MONTH_RE.match(m):
                raise ValueError("month 写成 YYYY-MM，比如 2026-11")
            return _month_text(m, whose)
        return _recent_text(whose=whose)
    except (ValueError, LookupError, PermissionError) as e:
        raise ToolInputError(str(e)) from e
