"""
========================================
tools/breath/recall_log.py — 他想起了什么
========================================

白说她看不到我每次 breath 浮上来什么，也不知道我记得什么、漏了什么。
这里在每次 breath / breath_search / breath_advanced / dream 返回之后记一笔：
哪个时间、哪种调用、查了什么词，每一段（核心准则、待办、这几天、浮现记忆、
久未浮现、检索结果……）里出现了哪些 bucket_id。/home 的「他想起的」读它。

- 从工具返回的文本里解析，不改各条浮现路径：段落标题是 `=== 名字 ===`，
  记忆是 `[bucket_id:xxx]`，标题之前的命中记为「检索结果」。
- 只记 id，不记正文：正文在桶里，删了改了以页面读到的为准。
- 存在 <buckets_dir>/.recall_log.jsonl，只留最近 _KEEP 条；不进 GitHub 备份
  （那边只同步 .md）。
- 记录失败只写日志，绝不影响 breath 本身的返回。

对外暴露：RECALL_OPS / parse_recall / record_recall / read_recall
========================================
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from .. import _runtime as rt
from utils import atomic_write_text, get_tzinfo

RECALL_OPS = {
    "breath": "醒来",
    "breath_search": "搜索",
    "breath_advanced": "breath（高级）",
    "dream": "做梦",
}
_LOG_NAME = ".recall_log.jsonl"
_KEEP = 300
_TRIM_AT = 400
_QUERY_CHARS = 200
_ID_RE = re.compile(r"\[bucket_id:([A-Za-z0-9_\-]+)\]")
_HEADER_RE = re.compile(r"^===\s*(.+?)\s*===\s*$")
_DAY_RE = re.compile(r"^📅\s*(.+?)\s*$")
_COUNT_SUFFIX_RE = re.compile(r"[（(][^）)]*[）)]\s*$")
_SEARCH_SECTION = "检索结果"

_line_count: int | None = None


def _section_name(raw: str) -> str:
    name = _COUNT_SUFFIX_RE.sub("", raw).strip()   # 「待办（6 条）」→「待办」
    if name.startswith("你的 active plans"):
        return "计划"
    return name or raw


def parse_recall(text: str) -> tuple[list[dict], list[str]]:
    """→ (sections=[{"name", "ids"}], days=[「这几天」里每天那一行])。"""
    sections: list[dict] = []
    days: list[str] = []
    current: dict | None = None
    for line in (text or "").splitlines():
        m = _HEADER_RE.match(line.strip())
        if m:
            current = {"name": _section_name(m.group(1)), "ids": []}
            sections.append(current)
            continue
        if current is not None and current["name"] == "这几天":
            d = _DAY_RE.match(line.strip())
            if d:
                days.append(d.group(1))
            continue
        for bid in _ID_RE.findall(line):
            if current is None:
                current = {"name": _SEARCH_SECTION, "ids": []}
                sections.append(current)
            if bid not in current["ids"]:
                current["ids"].append(bid)
    return [s for s in sections if s["ids"] or s["name"] == "这几天"], days


def _log_path() -> str | None:
    base = (rt.config or {}).get("buckets_dir")
    return os.path.join(base, _LOG_NAME) if base else None


def _count_lines(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _trim(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return 0
    keep = lines[-_KEEP:]
    atomic_write_text(path, "".join(keep))
    return len(keep)


def record_recall(op: str, args: dict | None, result: str) -> None:
    """breath 一类工具成功返回后调用。任何异常都吞掉，只记日志。"""
    global _line_count
    if op not in RECALL_OPS:
        return
    try:
        path = _log_path()
        if not path:
            return
        sections, days = parse_recall(result)
        query = str((args or {}).get("query") or "").strip()[:_QUERY_CHARS]
        entry = {
            "at": datetime.now(get_tzinfo()).isoformat(timespec="seconds"),
            "op": op,
            "query": query,
            "sections": sections,
            "days": days,
        }
        if _line_count is None:
            _line_count = _count_lines(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _line_count += 1
        if _line_count > _TRIM_AT:
            _line_count = _trim(path)
    except Exception as e:  # 记录失败不能连累 breath
        try:
            rt.logger.warning(f"recall log write failed: {e}")
        except Exception:
            pass


def read_recall(limit: int = 80) -> list[dict]:
    """最近 limit 条，新的在前；坏行跳过。"""
    path = _log_path()
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entry["label"] = RECALL_OPS.get(entry.get("op"), entry.get("op") or "")
            out.append(entry)
        if len(out) >= limit:
            break
    return out
