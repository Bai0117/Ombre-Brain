"""
========================================
tools/dedupe.py — 疑似重复的记忆
========================================

为什么会重复：新窗口的我 breath 不到某件事，就当第一次听见再记一遍；
hold 自己的合并只看同一个域里最像的一条，域打得不一样、或者那天向量
挂了（10.22 WARP 断过），就合不上。白说她不在乎多说一次，但同一件事
存好几条，会把浮现的位置占掉，改的时候也只改到其中一条。

这里做两件事：
1. find_pairs：把全部记忆两两比一遍向量（余弦），超过门槛的列成一对，
   给后台「重复」页。没有向量的记忆退回文字比对（字的二元组重合度）。
   她点「不是一回事」的对子记在 <buckets_dir>/.dupe_dismissed.json，不再出现。
   去掉哪条不在这里做：页面上「留这条」走原来的归档请求，要我确认。
2. similar_to_new：hold 新建一条之后，看看旧记忆里有没有很像的，
   有就在 hold 的回复里提醒我去补那条旧的。

旧的 check_duplicate_for（_common.py）会往两边写 dup_candidate，但
BucketManager.update 不认这个字段，写不进去；门槛 0.95 也几乎碰不到。
这里不依赖它。

对外暴露：find_pairs / dismiss_pair / similar_to_new / pair_key
========================================
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime

from . import _runtime as rt
from ._common import is_terminal_memory_metadata
from .plan.core import is_letter_bucket
from utils import atomic_write_text, get_tzinfo, parse_bool

DEFAULT_MIN_SCORE = 0.88
TEXT_MIN_SCORE = 0.45
HOLD_HINT_MIN_SCORE = 0.88
_TYPES = frozenset({"dynamic", "permanent"})
_DISMISSED_NAME = ".dupe_dismissed.json"
_MAX_PAIRS = 80
_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def pair_key(a: str, b: str) -> str:
    return "|".join(sorted((str(a), str(b))))


def _eligible(bucket: dict) -> bool:
    meta = bucket.get("metadata") or {}
    if str(meta.get("type") or "dynamic").strip().lower() not in _TYPES:
        return False
    if is_letter_bucket(bucket) or is_terminal_memory_metadata(meta):
        return False
    if parse_bool(meta.get("test_data"), default=False):
        return False
    return True


# ---------------- 「不是一回事」 ----------------

def _dismissed_path() -> str | None:
    base = (rt.config or {}).get("buckets_dir")
    return os.path.join(base, _DISMISSED_NAME) if base else None


def load_dismissed() -> dict:
    path = _dismissed_path()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def dismiss_pair(a: str, b: str) -> None:
    path = _dismissed_path()
    if not path:
        raise RuntimeError("buckets_dir 未配置")
    data = load_dismissed()
    data[pair_key(a, b)] = {"at": datetime.now(get_tzinfo()).isoformat(timespec="seconds")}
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=1))


# ---------------- 比对 ----------------

def _load_vectors(db_path: str, ids: set[str]):
    """→ (id 列表, 归一化后的矩阵)。读不到就返回空。"""
    import numpy as np

    from ombrebrain.storage.vector_codec import decode_vector

    if not db_path or not os.path.exists(db_path):
        return [], None
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT bucket_id, embedding FROM embeddings").fetchall()
    finally:
        conn.close()
    keep_ids, vecs, dim = [], [], None
    for bid, raw in rows:
        bid = str(bid)
        if bid not in ids:
            continue
        try:
            v = np.asarray(decode_vector(raw), dtype=np.float32)
        except Exception:
            continue
        if v.size == 0:
            continue
        if dim is None:
            dim = v.size
        if v.size != dim:
            continue
        n = float(np.linalg.norm(v))
        if n == 0:
            continue
        keep_ids.append(bid)
        vecs.append(v / n)
    if not vecs:
        return [], None
    return keep_ids, np.vstack(vecs)


def _bigrams(text: str) -> set[str]:
    s = _PUNCT_RE.sub("", str(text or "")).lower()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _compute(buckets: list[dict], db_path: str, min_score: float) -> list[tuple[str, str, float, str]]:
    import numpy as np

    by_id = {b["id"]: b for b in buckets}
    ids, mat = _load_vectors(db_path, set(by_id))
    found: dict[str, tuple[str, str, float, str]] = {}
    if mat is not None and len(ids) > 1:
        sims = mat @ mat.T
        iu = np.triu_indices(len(ids), k=1)
        vals = sims[iu]
        hit = np.nonzero(vals >= min_score)[0]
        for k in hit:
            i, j = int(iu[0][k]), int(iu[1][k])
            a, b = ids[i], ids[j]
            found[pair_key(a, b)] = (a, b, float(vals[k]), "vector")

    # 没有向量的（向量挂掉那天写的正好最容易重复）：退回文字比对
    with_vec = set(ids)
    missing = [bid for bid in by_id if bid not in with_vec]
    if missing:
        grams = {bid: _bigrams(by_id[bid].get("content")) for bid in by_id}
        for m in missing:
            for other in by_id:
                if other == m:
                    continue
                key = pair_key(m, other)
                if key in found:
                    continue
                score = _jaccard(grams[m], grams[other])
                if score >= TEXT_MIN_SCORE:
                    found[key] = (m, other, score, "text")
    return sorted(found.values(), key=lambda p: -p[2])


def _side(bucket: dict, deletion_status) -> dict:
    meta = bucket.get("metadata") or {}
    return {
        "id": bucket["id"],
        "name": meta.get("name", bucket["id"]),
        "created": meta.get("created", ""),
        "domain": meta.get("domain", []),
        "importance": meta.get("importance", 5),
        "pinned": parse_bool(meta.get("pinned"), default=False)
        or str(meta.get("type") or "") == "permanent",
        "content": bucket.get("content", ""),
        "deletion_request": deletion_status,
    }


async def find_pairs(
    min_score: float = DEFAULT_MIN_SCORE,
    *,
    deletion_requests=None,
    limit: int = _MAX_PAIRS,
) -> dict:
    """全库两两比对，返回疑似重复的对子（新的、像的在前）。"""
    all_b = await rt.bucket_mgr.list_all(include_archive=False)
    buckets = [b for b in all_b if _eligible(b)]
    engine = rt.embedding_engine
    db_path = getattr(engine, "db_path", "") if engine is not None else ""
    raw = await asyncio.to_thread(_compute, buckets, db_path, float(min_score))

    dismissed = load_dismissed()
    statuses = {}
    if deletion_requests is not None:
        try:
            statuses = deletion_requests.status_snapshot() or {}
        except Exception:
            statuses = {}
    by_id = {b["id"]: b for b in buckets}
    pairs = []
    for a, b, score, kind in raw:
        if pair_key(a, b) in dismissed:
            continue
        # 一对里两条都是核心准则的不列：那是她亲手钉的，要改去核心准则改
        ba, bb = by_id[a], by_id[b]
        sa, sb = _side(ba, statuses.get(a)), _side(bb, statuses.get(b))
        if sa["pinned"] and sb["pinned"]:
            continue
        # 旧的放左边
        if str(sa["created"]) > str(sb["created"]):
            sa, sb = sb, sa
        pairs.append({"a": sa, "b": sb, "score": round(score, 4), "kind": kind})
        if len(pairs) >= limit:
            break
    return {
        "pairs": pairs,
        "total": len(pairs),
        "min_score": min_score,
        "vector_ready": bool(db_path) and os.path.exists(db_path),
    }


# ---------------- hold 之后的提醒 ----------------

async def similar_to_new(new_id: str, content: str, *, timeout: float = 6.0) -> str:
    """新建的这条和哪条旧记忆很像？→ 给 hold 回复追加的一行提示，没有就返回 ""。

    merge_or_create 刚用同一段正文做过检索，查询向量在缓存里，这里通常不再花一次调用。
    任何失败都安静地返回 ""：提醒不能连累 hold。
    """
    engine = rt.embedding_engine
    if engine is None or not getattr(engine, "enabled", False):
        return ""
    try:
        sims = await asyncio.wait_for(engine.search_similar(content, top_k=6), timeout)
    except Exception as e:
        rt.logger.info(f"dedupe hint skipped: {e}")
        return ""
    dismissed = load_dismissed()
    for bid, score in sims or []:
        if bid == new_id or float(score) < HOLD_HINT_MIN_SCORE:
            continue
        if pair_key(new_id, bid) in dismissed:
            continue
        try:
            old = await rt.bucket_mgr.get(bid)
        except Exception:
            old = None
        if not old or not _eligible(old):
            continue
        meta = old.get("metadata") or {}
        name = meta.get("name", bid)
        return (
            f"🔁 这条和旧记忆 [bucket_id:{bid}]「{name}」很像（相似度 {float(score):.2f}）。"
            f"要是同一件事：用 trace 把这次新的细节补进那条旧的，"
            f"再请她在后台「重复」页留那条、把这条归档。"
        )
    return ""
