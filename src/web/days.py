"""
========================================
web/days.py — 后台「日子」页
========================================

GET    /api/days          全部日子 + 今天 breath 里那段「日子」的原文
POST   /api/days          加一个 {date, title, yearly, count}
DELETE /api/days/{id}     删一个

实现在 tools/days（和 MCP 的 days 工具同一份数据）。
对外暴露：register(mcp)
========================================
"""

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh


def register(mcp) -> None:
    @mcp.custom_route("/api/days", methods=["GET", "POST"])
    async def api_days(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.days import add_day, days_section, load_days

        err = sh._require_auth(request)
        if err:
            return err
        if request.method == "POST":
            try:
                body = await sh._read_json_object(request)
            except Exception:
                return JSONResponse({"error": "body 要是 JSON 对象"}, status_code=400)
            try:
                item = add_day(
                    str(body.get("date") or ""),
                    str(body.get("title") or ""),
                    yearly=bool(body.get("yearly", True)),
                    count=bool(body.get("count", False)),
                )
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            return JSONResponse({"ok": True, "day": item})
        return JSONResponse({"days": load_days(), "section": days_section()})

    @mcp.custom_route("/api/days/{day_id}", methods=["DELETE"])
    async def api_day_delete(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.days import delete_day

        err = sh._require_auth(request)
        if err:
            return err
        if not delete_day(request.path_params["day_id"]):
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"ok": True})
