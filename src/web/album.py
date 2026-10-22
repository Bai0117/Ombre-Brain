"""
========================================
web/album.py — 后台「相册」页
========================================

GET    /api/album                  全部照片的信息（不含图片本身）
POST   /api/album                  上传一张 {data: base64, caption, taken, memory_id, w, h}
                                   （浏览器先缩到长边 1600、转 JPEG）
POST   /api/album/{id}             改说明 {caption?, taken?, memory_id?}
DELETE /api/album/{id}             删掉
GET    /api/album/{id}/image       图片本身（要登录；<img> 带着 cookie 就能看）

实现在 tools/album（和 MCP 的 album 工具同一份数据）。
对外暴露：register(mcp)
========================================
"""

import base64
import binascii

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh

# base64 比原文大约 4/3，再留点余量；真正的上限在 tools/album 里按字节判。
_MAX_BODY_CHARS = 4 * 1024 * 1024


def register(mcp) -> None:
    @mcp.custom_route("/api/album", methods=["GET", "POST"])
    async def api_album(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.album import add_photo, load_photos

        err = sh._require_auth(request)
        if err:
            return err
        if request.method == "GET":
            photos = sorted(
                load_photos(),
                key=lambda p: (p.get("taken") or str(p.get("added", ""))[:10], p.get("added", "")),
                reverse=True,
            )
            return JSONResponse({"photos": photos})
        try:
            body = await sh._read_json_object(request)
        except Exception:
            return JSONResponse({"error": "body 要是 JSON 对象"}, status_code=400)
        raw = str(body.get("data") or "")
        if "," in raw[:100] and raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        if len(raw) > _MAX_BODY_CHARS:
            return JSONResponse({"error": "照片太大"}, status_code=413)
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            return JSONResponse({"error": "照片数据坏了"}, status_code=400)
        try:
            item = add_photo(
                data,
                caption=str(body.get("caption") or ""),
                taken=str(body.get("taken") or ""),
                memory_id=str(body.get("memory_id") or ""),
                width=int(body.get("w") or 0),
                height=int(body.get("h") or 0),
            )
        except (ValueError, TypeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "photo": item})

    @mcp.custom_route("/api/album/{photo_id}", methods=["POST", "DELETE"])
    async def api_album_item(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.album import delete_photo, update_photo

        err = sh._require_auth(request)
        if err:
            return err
        photo_id = request.path_params["photo_id"]
        if request.method == "DELETE":
            if not delete_photo(photo_id):
                return JSONResponse({"error": "not found"}, status_code=404)
            return JSONResponse({"ok": True})
        try:
            body = await sh._read_json_object(request)
        except Exception:
            return JSONResponse({"error": "body 要是 JSON 对象"}, status_code=400)
        try:
            item = update_photo(
                photo_id,
                caption=body.get("caption"),
                taken=body.get("taken"),
                memory_id=body.get("memory_id"),
            )
        except KeyError:
            return JSONResponse({"error": "not found"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({"ok": True, "photo": item})

    @mcp.custom_route("/api/album/{photo_id}/image", methods=["GET"])
    async def api_album_image(request: Request) -> Response:
        from starlette.responses import JSONResponse
        from tools.album import read_photo

        err = sh._require_auth(request)
        if err:
            return err
        got = read_photo(request.path_params["photo_id"])
        if got is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        data, mime = got
        return Response(
            data,
            media_type=mime,
            headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
        )
