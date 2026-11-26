"""
========================================
tools/note/__init__.py — note 工具入口（小记）
========================================

我的小日记：一句话、一个日子、一个团子（1~10 的心情）。
她能在后台「小记」页看、评论；我能回她的评论。

对外暴露：
- dispatch(...)：note 工具
- load_notes / add_note / add_comment / delete_comment / is_deleted / mark_seen：给后台 /api/notes 用
- notes_section()：无参 breath 的「小记」段
========================================
"""

from .core import (  # noqa: F401
    MOOD_MAX,
    MOOD_MIN,
    add_comment,
    add_note,
    delete_comment,
    dispatch,
    is_deleted,
    load_notes,
    mark_seen,
    notes_section,
)
