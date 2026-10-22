"""
========================================
web/shelf.py — 后台「书架」页
========================================

GET    /api/shelf          全部条目 + 口味（taste）
POST   /api/shelf          加或改一项 {title, kind, creator, status, finished, rating, tags, note, id?}
DELETE /api/shelf/{id}     拿掉一项

实现在 tools/shelf（和 MCP 的 shelf 工具同一份数据）。
对外暴露：register(mcp)
========================================
"""

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh


def register(mcp) -> None:
    @mcp.custom_route("/api/shelf", methods=["GET", "POST"])
    async def api_shelf(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.shelf import KINDS, STATUSES, load_items, save_item, taste

        err = sh._require_auth(request)
        if err:
            return err
        if request.method == "POST":
            try:
                body = await sh._read_json_object(request)
            except Exception:
                return JSONResponse({"error": "body 要是 JSON 对象"}, status_code=400)
            rating = body.get("rating")
            try:
                item, created = save_item(
                    title=str(body.get("title") or ""),
                    kind=str(body.get("kind") or "") or None,
                    creator=None if body.get("creator") is None else str(body.get("creator")),
                    status=str(body.get("status") or "") or None,
                    finished=None if body.get("finished") is None else str(body.get("finished")),
                    rating=None if rating in (None, "") else rating,
                    tags=body.get("tags"),
                    note=None if body.get("note") is None else str(body.get("note")),
                    item_id=str(body.get("id") or ""),
                )
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            return JSONResponse({"ok": True, "item": item, "created": created})
        return JSONResponse({
            "items": load_items(),
            "taste": taste(),
            "kinds": KINDS,
            "statuses": STATUSES,
        })

    @mcp.custom_route("/api/shelf/{item_id}", methods=["DELETE"])
    async def api_shelf_delete(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.shelf import delete_item

        err = sh._require_auth(request)
        if err:
            return err
        if not delete_item(request.path_params["item_id"]):
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"ok": True})
