"""
========================================
web/recall.py — 他想起的
========================================

- GET /api/recall-log      最近几十次 breath / breath_search / dream 各浮上来哪些记忆，
                            附带每条记忆的开头（旧后台没有整份记忆列表可对）
- GET /api/breath-preview  现在 breath 一次：和我醒来时调用的是同一个默认浮现，
                            拆成段落 → 记忆卡片给后台看

预览只是看：默认浮现本来就不 touch（不改衰减计时、不加激活次数），这里也不经过
MCP 包装，所以不记进「他想起的」历史，也不会把删除请求通知「读掉」。
都需要登录，和其它 /api 一样。
========================================
"""

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh

_PREVIEW_CHARS = 90


async def _memory_previews(ids: list[str]) -> dict:
    """id → {preview, created, type, pinned, missing}；锁着的信不给正文。"""
    from tools.plan.core import letter_lock_state

    out: dict = {}
    for bid in ids:
        try:
            b = await sh.bucket_mgr.get(bid)
        except Exception:
            b = None
        if not b:
            out[bid] = {"missing": True}
            continue
        meta = b.get("metadata") or {}
        if letter_lock_state(b, "human")["locked"]:
            text = "这封信还锁着。"
        else:
            text = " ".join(str(b.get("content") or meta.get("name") or "").split())
            if len(text) > _PREVIEW_CHARS:
                text = text[:_PREVIEW_CHARS] + "…"
        out[bid] = {
            "preview": text,
            "created": str(meta.get("created") or ""),
            "type": str(meta.get("type") or "dynamic"),
            "pinned": bool(meta.get("pinned")),
            "missing": False,
        }
    return out


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
        entries = read_recall(limit)
        body: dict = {"entries": entries}
        if request.query_params.get("with") == "previews":
            ids: list[str] = []
            for e in entries:
                for sec in e.get("sections") or []:
                    for bid in sec.get("ids") or []:
                        if bid not in ids:
                            ids.append(bid)
            body["memories"] = await _memory_previews(ids)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/api/breath-preview", methods=["GET"])
    async def api_breath_preview(request: Request) -> Response:
        from starlette.responses import JSONResponse

        from tools import _runtime as rt
        from tools.breath import _with_them
        from tools.breath.recall_log import parse_breath_blocks
        from tools.breath.surface import surface_default

        err = sh._require_auth(request)
        if err:
            return err
        try:
            await rt.decay_engine.ensure_started()
            cfg = (rt.config or {}).get("surfacing", {}) or {}
            text = await _with_them(await surface_default(
                max_results=min(int(cfg.get("breath_max_results") or 20), 50),
                max_tokens=min(int(cfg.get("breath_max_tokens") or 20000), 40000),
                tag_filter=[],
            ))
        except Exception as e:
            return JSONResponse({"error": f"breath 失败：{type(e).__name__}"}, status_code=500)
        return JSONResponse(
            {"text": text, "sections": parse_breath_blocks(text)},
            headers={"Cache-Control": "no-store"},
        )
