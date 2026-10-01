"""
========================================
tools/now/__init__.py — now 工具入口
========================================

now() 是「看一眼现在几点」。不读桶、不调 LLM、不需要用户位置，只读服务器
自己的时钟，按 config.yaml 的 timezone 换算。

对外暴露：
- dispatch() → str（与 server.py 中的 now tool 对应，0 参数）
- format_now(moment) → str，给测试和其他需要同一格式的地方复用
========================================
"""

from .core import format_now, now_core as dispatch  # noqa: F401
