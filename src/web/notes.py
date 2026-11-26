"""
========================================
web/notes.py — 后台「小记」页
========================================

GET  /api/notes?month=YYYY-MM          那个月的全部小记（带评论）；不给 month 就是全部
POST /api/notes/{note_id}/comments     她评论或回复 {content, reply_to?}
POST /api/notes/seen                   她点开看过了 {note_ids: [...]}，把我的回复标成已读

小记只有他（MCP 的 note 工具）能写，这里没有写小记、也没有删除的入口。
实现在 tools/note（和 MCP 的 note 工具同一份数据）。
对外暴露：register(mcp)
========================================
"""

import re

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def register(mcp) -> None:
    @mcp.custom_route("/api/notes", methods=["GET"])
    async def api_notes(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.note import MOOD_MAX, MOOD_MIN, load_notes

        err = sh._require_auth(request)
        if err:
            return err
        month = (request.query_params.get("month") or "").strip()
        if month and not _MONTH_RE.match(month):
            return JSONResponse({"error": "month 写成 YYYY-MM"}, status_code=400)
        notes = load_notes()
        unseen_total = sum(
            1 for n in notes for c in n["comments"]
            if c.get("author") == "ai" and not c.get("seen")
        )
        if month:
            notes = [n for n in notes if str(n.get("date", "")).startswith(month)]
        notes.sort(key=lambda n: (str(n.get("date") or ""), str(n.get("created") or "")))
        return JSONResponse({
            "notes": notes,
            "mood_min": MOOD_MIN,
            "mood_max": MOOD_MAX,
            "unseen_replies": unseen_total,
        })

    @mcp.custom_route("/api/notes/seen", methods=["POST"])
    async def api_notes_seen(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.note import mark_seen

        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await sh._read_json_object(request)
        except Exception:
            return JSONResponse({"error": "body 要是 JSON 对象"}, status_code=400)
        ids = body.get("note_ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return JSONResponse({"error": "note_ids 要是字符串数组"}, status_code=400)
        return JSONResponse({"ok": True, "marked": mark_seen("user", ids)})

    @mcp.custom_route("/api/notes/{note_id}/comments", methods=["POST"])
    async def api_note_comment(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.note import add_comment

        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await sh._read_json_object(request)
        except Exception:
            return JSONResponse({"error": "body 要是 JSON 对象"}, status_code=400)
        try:
            comment = add_comment(
                request.path_params["note_id"],
                "user",
                str(body.get("content") or ""),
                reply_to=str(body.get("reply_to") or ""),
            )
        except LookupError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "comment": comment})
