"""now()：0 参数报时。

客户端自带的时间工具可能要求共享位置才能用；记忆系统跑在一台有时钟、
配了时区的机器上，自己报时。这里钉住三件事：格式、时区来源、0 参数。
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

from tools import now as now_tool
from tools.now import core as now_core

_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} 周[一二三四五六日] \d{2}:\d{2}:\d{2}"
    r"（(凌晨|清晨|上午|中午|下午|晚上|深夜)）\| \S+$"
)


@pytest.mark.parametrize(
    ("hour", "period"),
    [
        (0, "凌晨"), (4, "凌晨"), (5, "清晨"), (7, "清晨"), (8, "上午"),
        (10, "上午"), (11, "中午"), (12, "中午"), (13, "下午"), (17, "下午"),
        (18, "晚上"), (22, "晚上"), (23, "深夜"),
    ],
)
def test_period_boundaries(hour, period):
    assert now_core._period(hour) == period


def test_format_now_is_one_human_and_machine_readable_line():
    moment = datetime(2026, 10, 2, 13, 42, 7, tzinfo=timezone(timedelta(hours=8)))
    assert now_tool.format_now(moment) == (
        "2026-10-02 周五 13:42:07（下午）| 2026-10-02T13:42:07+08:00"
    )


@pytest.mark.asyncio
async def test_now_uses_configured_timezone(monkeypatch):
    tz = timezone(timedelta(hours=-5))
    monkeypatch.setattr(now_core, "get_tzinfo", lambda: tz)
    out = await now_tool.dispatch()
    assert _LINE.match(out), out
    stamp = datetime.fromisoformat(out.rsplit("| ", 1)[1])
    assert stamp.utcoffset() == timedelta(hours=-5)
    assert abs((stamp - datetime.now(timezone.utc)).total_seconds()) < 5


@pytest.mark.asyncio
async def test_now_tool_is_registered_with_zero_parameters():
    import server

    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert "now" in tools
    assert not (tools["now"].inputSchema.get("properties") or {})
    assert _LINE.match(await server.now())
