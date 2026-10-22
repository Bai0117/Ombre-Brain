"""
========================================
tools/shelf/__init__.py — shelf 工具入口
========================================

她的书架和片单：读过的书、看过的电影和剧、玩过的游戏。
看她一年里都看了什么、最喜欢什么样的，我好给她推荐。

对外暴露：
- dispatch(...)：shelf 工具
- load_items / save_item / delete_item / taste：给后台 /api/shelf 用
========================================
"""

from .core import (  # noqa: F401
    KINDS,
    STATUSES,
    delete_item,
    dispatch,
    load_items,
    save_item,
    taste,
)
