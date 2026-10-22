"""
========================================
tools/album/core.py — 相册
========================================

存放：
  <buckets_dir>/_album/<id>.<jpg|png|webp>   照片本身
  <buckets_dir>/.album.json                  {"photos": [{id, file, mime, caption, taken, added, memory_id, w, h}]}

- 上传只走后台：浏览器先把照片缩到长边 1600、转成 JPEG 再传（省流量，也省我看图的额度），
  服务器只认 JPEG / PNG / WebP 的文件头，单张最多 _MAX_BYTES。
- caption 是「图上是什么、那天发生了什么」——她可以写，我看过之后也可以补。
- taken 是照片那天（YYYY-MM-DD，可空）；memory_id 可以连到一条记忆。

还没进 GitHub 备份：备份每次都会把所有文件重新传一遍，照片多了会很重，
要先让备份学会跳过没变的文件再说。

对外暴露：load_photos / add_photo / update_photo / delete_photo / read_photo / dispatch
========================================
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime

from .. import _runtime as rt
from errors import ToolInputError
from utils import atomic_write_text, get_tzinfo

_INDEX = ".album.json"
_DIR = "_album"
_MAX_BYTES = 3 * 1024 * 1024  # 后台请求体上限是 4 MB，base64 要多三分之一
_CAPTION_MAX = 2000
_LIST_DEFAULT = 30
_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _base() -> str:
    base = (rt.config or {}).get("buckets_dir")
    if not base:
        raise RuntimeError("buckets_dir 未配置")
    return base


def _index_path() -> str:
    return os.path.join(_base(), _INDEX)


def _photo_dir() -> str:
    path = os.path.join(_base(), _DIR)
    os.makedirs(path, exist_ok=True)
    return path


def sniff_mime(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def load_photos() -> list[dict]:
    try:
        with open(_index_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, RuntimeError):
        return []
    photos = data.get("photos") if isinstance(data, dict) else None
    return [p for p in (photos or []) if isinstance(p, dict) and p.get("id") and p.get("file")]


def _save(photos: list[dict]) -> None:
    atomic_write_text(_index_path(), json.dumps({"photos": photos}, ensure_ascii=False, indent=1))


def _clean_taken(taken) -> str:
    raw = str(taken or "").strip()
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as e:
        raise ValueError("taken 写成 YYYY-MM-DD") from e


def add_photo(
    data: bytes,
    *,
    caption: str = "",
    taken: str = "",
    memory_id: str = "",
    width: int = 0,
    height: int = 0,
) -> dict:
    if not data:
        raise ValueError("照片是空的")
    if len(data) > _MAX_BYTES:
        raise ValueError(f"照片太大（最多 {_MAX_BYTES // 1024 // 1024} MB）")
    mime = sniff_mime(data)
    if mime is None:
        raise ValueError("只收 JPEG / PNG / WebP 照片")
    photo_id = "p_" + secrets.token_hex(4)
    filename = f"{photo_id}.{_EXT[mime]}"
    target = os.path.join(_photo_dir(), filename)
    tmp = target + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    item = {
        "id": photo_id,
        "file": filename,
        "mime": mime,
        "bytes": len(data),
        "caption": str(caption or "").strip()[:_CAPTION_MAX],
        "taken": _clean_taken(taken),
        "added": datetime.now(get_tzinfo()).isoformat(timespec="seconds"),
        "memory_id": str(memory_id or "").strip()[:64],
        "w": int(width or 0),
        "h": int(height or 0),
    }
    photos = load_photos()
    photos.append(item)
    _save(photos)
    return item


def update_photo(photo_id: str, *, caption=None, taken=None, memory_id=None) -> dict:
    photos = load_photos()
    item = next((p for p in photos if p["id"] == photo_id), None)
    if item is None:
        raise KeyError(photo_id)
    if caption is not None:
        item["caption"] = str(caption).strip()[:_CAPTION_MAX]
    if taken is not None:
        item["taken"] = _clean_taken(taken)
    if memory_id is not None:
        item["memory_id"] = str(memory_id).strip()[:64]
    _save(photos)
    return item


def delete_photo(photo_id: str) -> bool:
    photos = load_photos()
    item = next((p for p in photos if p["id"] == photo_id), None)
    if item is None:
        return False
    _save([p for p in photos if p["id"] != photo_id])
    try:
        os.remove(os.path.join(_photo_dir(), os.path.basename(item["file"])))
    except OSError:
        pass
    return True


def read_photo(photo_id: str) -> tuple[bytes, str] | None:
    item = next((p for p in load_photos() if p["id"] == photo_id), None)
    if item is None:
        return None
    path = os.path.join(_photo_dir(), os.path.basename(item["file"]))
    try:
        with open(path, "rb") as f:
            data = f.read(_MAX_BYTES + 1)
    except OSError:
        return None
    if len(data) > _MAX_BYTES:
        return None
    return data, item.get("mime") or sniff_mime(data) or "image/jpeg"


def _sort_key(p: dict):
    return (p.get("taken") or p.get("added", "")[:10], p.get("added", ""))


def _line(p: dict) -> str:
    when = p.get("taken") or str(p.get("added", ""))[:10]
    cap = " ".join(str(p.get("caption") or "").split())
    if len(cap) > 80:
        cap = cap[:80] + "…"
    link = f" · 连着记忆 {p['memory_id']}" if p.get("memory_id") else ""
    return f"[photo_id:{p['id']}] {when} {cap or '（还没有说明）'}{link}"


def _search(query: str, photos: list[dict]) -> list[dict]:
    q = query.strip().lower()
    return [
        p for p in photos
        if q in str(p.get("caption", "")).lower()
        or q in str(p.get("taken", ""))
        or q == str(p.get("memory_id", "")).lower()
    ]


async def dispatch(
    photo_id: str = "",
    caption: str = "",
    taken: str = "",
    memory_id: str = "",
    query: str = "",
    limit: int = 0,
) -> tuple[str, bytes | None, str]:
    photo_id = str(photo_id or "").strip()
    if photo_id:
        changes = {}
        if caption:
            changes["caption"] = caption
        if taken:
            changes["taken"] = taken
        if memory_id:
            changes["memory_id"] = memory_id
        if changes:
            try:
                item = update_photo(photo_id, **changes)
            except KeyError as e:
                raise ToolInputError(f"相册里没有 {photo_id}。") from e
            except ValueError as e:
                raise ToolInputError(str(e)) from e
            return "改好了：\n" + _line(item), None, ""
        item = next((p for p in load_photos() if p["id"] == photo_id), None)
        if item is None:
            raise ToolInputError(f"相册里没有 {photo_id}。")
        got = read_photo(photo_id)
        if got is None:
            raise ToolInputError(f"{photo_id} 的照片文件读不到了。")
        data, mime = got
        text = _line(item)
        if not item.get("caption"):
            text += "\n这张还没有说明：看完写一句图上是什么、那天发生了什么，album(photo_id=..., caption=...)。"
        return text, data, mime

    photos = sorted(load_photos(), key=_sort_key, reverse=True)
    if not photos:
        return ("相册还是空的。她在后台「相册」页上传照片之后，这里就能看到；"
                "用 album(photo_id=...) 把一张拿回来看。"), None, ""
    if query:
        hits = _search(query, photos)
        if not hits:
            return f"相册里没有和「{query}」有关的照片。", None, ""
        return f"=== 相册里「{query}」{len(hits)} 张 ===\n" + "\n".join(_line(p) for p in hits), None, ""
    n = max(1, min(int(limit or _LIST_DEFAULT), 200))
    head = f"=== 相册 {len(photos)} 张（新→旧，前 {min(n, len(photos))} 张）==="
    tail = "\n用 album(photo_id=...) 看某一张。" if photos else ""
    return head + "\n" + "\n".join(_line(p) for p in photos[:n]) + tail, None, ""
