"""
========================================
web/notes.py — 后台「小记」页
========================================

GET  /api/notes?month=YYYY-MM          那个月两块板的全部小记（带评论、带 author）；不给 month 就是全部
POST /api/notes                        她在自己的板上写一条 {content, mood?, date?}
PATCH /api/notes/{note_id}             她改自己的小记 {content?, mood?, date?}（旧版本留在 history）
DELETE /api/notes/{note_id}            她删自己的小记（只改可见性，原文留在文件里）
POST /api/notes/{note_id}/comments     她评论或回复 {content, reply_to?}
POST /api/notes/seen                   她点开看过了 {note_ids: [...]}，把我的回复标成已读
DELETE /api/notes/{note_id}/comments/{comment_id}
                                       她删掉自己的评论（只改可见性，原文留在文件里）

他的板只有他（MCP 的 note 工具）能写，这里改不了也删不了他的小记和评论；
她的板只有她在这里写，她能改能删自己的。
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
        from tools.note import MOOD_MAX, MOOD_MIN, is_deleted, load_notes, note_author

        err = sh._require_auth(request)
        if err:
            return err
        month = (request.query_params.get("month") or "").strip()
        if month and not _MONTH_RE.match(month):
            return JSONResponse({"error": "month 写成 YYYY-MM"}, status_code=400)
        notes = [n for n in load_notes() if not is_deleted(n)]
        unseen_by_board = {"ai": 0, "user": 0}
        for n in notes:
            n["author"] = note_author(n)
            n.pop("history", None)          # 改之前的版本不发给页面
            unseen_by_board[n["author"]] += sum(
                1 for c in n["comments"] if c.get("author") == "ai" and not c.get("seen")
            )
        unseen_total = sum(unseen_by_board.values())
        if month:
            notes = [n for n in notes if str(n.get("date", "")).startswith(month)]
        # 同一秒写下的几条按写入先后排；文件里本来就是写入顺序，稳定排序会保住它。
        notes.sort(key=lambda n: (str(n.get("date") or ""), str(n.get("created") or "")))
        # 删掉的评论只留个壳（页面要知道「回的那条删了」），原文不发出去。
        for n in notes:
            n["comments"] = [
                {"id": c.get("id"), "author": c.get("author"), "reply_to": c.get("reply_to", ""),
                 "created": c.get("created"), "deleted": True, "content": "", "seen": True}
                if is_deleted(c) else c
                for c in n["comments"]
            ]
        return JSONResponse({
            "notes": notes,
            "mood_min": MOOD_MIN,
            "mood_max": MOOD_MAX,
            "unseen_replies": unseen_total,
            "unseen_by_board": unseen_by_board,
        })

    @mcp.custom_route("/api/notes", methods=["POST"])
    async def api_note_create(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.note import add_note

        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await sh._read_json_object(request)
        except Exception:
            return JSONResponse({"error": "body 要是 JSON 对象"}, status_code=400)
        try:
            note = add_note(
                str(body.get("content") or ""), mood=body.get("mood") or 0,
                note_date=str(body.get("date") or ""), author="user",
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "note": note})

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

    @mcp.custom_route("/api/notes/{note_id}/comments/{comment_id}", methods=["DELETE"])
    async def api_note_comment_delete(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.note import delete_comment

        err = sh._require_auth(request)
        if err:
            return err
        try:
            delete_comment(request.path_params["note_id"], request.path_params["comment_id"], "user")
        except LookupError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except PermissionError as e:
            return JSONResponse({"error": str(e)}, status_code=403)
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/notes/{note_id}", methods=["PATCH", "DELETE"])
    async def api_note_edit_or_delete(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.note import delete_note, edit_note

        err = sh._require_auth(request)
        if err:
            return err
        note_id = request.path_params["note_id"]
        try:
            if request.method == "DELETE":
                delete_note(note_id, "user")
                return JSONResponse({"ok": True})
            try:
                body = await sh._read_json_object(request)
            except Exception:
                return JSONResponse({"error": "body 要是 JSON 对象"}, status_code=400)
            note = edit_note(
                note_id, "user",
                content=body.get("content"), mood=body.get("mood"), note_date=body.get("date"),
            )
        except LookupError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        except PermissionError as e:
            return JSONResponse({"error": str(e)}, status_code=403)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        out = dict(note)
        out.pop("history", None)
        return JSONResponse({"ok": True, "note": out})
