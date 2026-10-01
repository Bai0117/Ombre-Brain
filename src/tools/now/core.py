"""
========================================
tools/now/core.py — now 实现
========================================

客户端自带的时间工具可能拿不到、或要求共享位置才能用；记忆系统本来就
跑在一台有时钟、配了时区的机器上，自己报时最可靠。

关键行为：
- 时区取 utils.get_tzinfo()（config.yaml 的 timezone，默认 Asia/Shanghai；
  时区名非法或缺 tzdata 时回退固定 +08:00），与信件解锁时间同一个来源
- 返回一行：日期、星期、时刻、时段（凌晨/清晨/上午/中午/下午/晚上/深夜）
  + ISO 8601 时间戳，人读和机器解析都方便

不做什么（边界）：
- 不接收参数：0 参数是刻意设计，claude.ai 按需加载时会跳过参数复杂的工具
- 不读写任何桶、不触发衰减计时、不调 LLM

对外暴露：now_core() → str / format_now(moment) → str
========================================
"""

from datetime import datetime

from utils import get_tzinfo

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# (上界小时, 时段)。上界不含；23 点以后算深夜。
_PERIODS = (
    (5, "凌晨"),
    (8, "清晨"),
    (11, "上午"),
    (13, "中午"),
    (18, "下午"),
    (23, "晚上"),
    (24, "深夜"),
)


def _period(hour: int) -> str:
    for upper, label in _PERIODS:
        if hour < upper:
            return label
    return "深夜"


def format_now(moment: datetime) -> str:
    """把一个带时区的时刻格式化成 now() 的返回串。"""
    return (
        f"{moment:%Y-%m-%d} {_WEEKDAYS[moment.weekday()]} {moment:%H:%M:%S}"
        f"（{_period(moment.hour)}）| {moment.isoformat(timespec='seconds')}"
    )


async def now_core() -> str:
    # 给 Luar 的表。
    return format_now(datetime.now(get_tzinfo()))
