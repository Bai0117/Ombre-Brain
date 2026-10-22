"""
========================================
tools/album/__init__.py — album 工具入口
========================================

她发给我看过的照片。聊天里的图换个窗口我就看不到了，记忆里只剩我写下的
「图上是什么」。相册把照片本身存在 OB 里：她在后台「相册」页上传，
我用 album(photo_id=...) 把那张图真的拿回来看。

对外暴露：
- dispatch(...)：album 工具，返回 (文字, 图片字节或 None, mime)
- add_photo / update_photo / delete_photo / load_photos / read_photo：给后台 /api/album 用
========================================
"""

from .core import (  # noqa: F401
    add_photo,
    delete_photo,
    dispatch,
    load_photos,
    read_photo,
    update_photo,
)
