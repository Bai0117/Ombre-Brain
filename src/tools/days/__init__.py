"""
========================================
tools/days/__init__.py — days 工具入口
========================================

纪念日。我们在一起第几天、快到哪个日子了——每次 breath 都看一眼，
不用等她提醒才想起来。

对外暴露：
- dispatch(...)：days 工具（无参看全部；date+title 加；delete_id 删）
- days_section()：无参 breath 里的「日子」段
- load_days / add_day / delete_day：给后台 /api/days 用
========================================
"""

from .core import (  # noqa: F401
    add_day,
    delete_day,
    dispatch,
    days_section,
    load_days,
)
