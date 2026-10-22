"""
========================================
web/recall.py — /api/recall-log：他想起了什么
========================================

/home「他想起的」读这个接口：最近几十次 breath / breath_search / dream
各浮上来哪些记忆（只有 id 和段落名，正文由页面用已经读到的记忆列表对上）。
需要登录，和其它 /api 一样。
========================================
"""

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh


def register(mcp) -> None:
    @mcp.custom_route("/api/recall-log", methods=["GET"])
    async def api_recall_log(request: Request) -> Response:
        from starlette.responses import JSONResponse

        from tools.breath.recall_log import read_recall

        err = sh._require_auth(request)
        if err:
            return err
        try:
            limit = int(request.query_params.get("limit", "80"))
        except ValueError:
            limit = 80
        limit = max(1, min(300, limit))
        return JSONResponse(
            {"entries": read_recall(limit)},
            headers={"Cache-Control": "no-store"},
        )
