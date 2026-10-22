"""GitHub 备份带上日子、书架和「不是一回事」的对子（vault 根目录的小 JSON）。"""

from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest

from github_sync import GitHubSync, _is_backup_relative_path


def _resp(method, url, payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request(method, url))


@pytest.mark.asyncio
async def test_sync_uploads_root_sidecars_only(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    (vault / "dynamic").mkdir(parents=True)
    (vault / "dynamic" / "a.md").write_text("---\nid: a\n---\n正文", encoding="utf-8")
    (vault / ".days.json").write_text('{"days": []}', encoding="utf-8")
    (vault / ".shelf.json").write_text('{"items": []}', encoding="utf-8")
    (vault / ".dupe_dismissed.json").write_text("{}", encoding="utf-8")
    (vault / ".recall_log.jsonl").write_text("{}\n", encoding="utf-8")       # 不备份
    (vault / "dynamic" / ".days.json").write_text("{}", encoding="utf-8")      # 不在根目录，不备份
    sync = GitHubSync(token="t", repo="owner/repo")
    captured = {}

    async def capture(files):
        captured.update({p: files[p] for p in files})
        return len(files)

    monkeypatch.setattr(sync, "_batch_commit", capture)
    result = await sync.sync(str(vault))

    assert result["ok"] is True
    assert set(captured) == {"dynamic/a.md", ".days.json", ".shelf.json", ".dupe_dismissed.json"}


def test_restore_accepts_only_known_sidecar_names():
    assert _is_backup_relative_path(".days.json")
    assert _is_backup_relative_path(".shelf.json")
    assert not _is_backup_relative_path(".recall_log.jsonl")
    assert not _is_backup_relative_path("dynamic/.days.json")
    assert not _is_backup_relative_path("config.json")


def _fake_github(files: dict[str, bytes]):
    manifest = {
        "schema_version": 1,
        "generated_at": "2026-10-22T00:00:00+00:00",
        "file_count": len(files),
        "total_bytes": sum(len(v) for v in files.values()),
        "files": [
            {"path": p, "bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()}
            for p, v in files.items()
        ],
    }
    manifest_data = json.dumps(manifest).encode()
    blobs = {"manifest-sha": manifest_data}
    tree = [{"type": "blob", "path": "ombre/_ombre_backup_manifest.json",
             "sha": "manifest-sha", "size": len(manifest_data)}]
    for i, (p, v) in enumerate(files.items()):
        blobs[f"sha{i}"] = v
        tree.append({"type": "blob", "path": f"ombre/{p}", "sha": f"sha{i}", "size": len(v)})

    async def fake_request(_client, method, url, **_kwargs):
        if url.endswith("/git/ref/heads/main"):
            return _resp(method, url, {"object": {"sha": "head-sha"}})
        if url.endswith("/git/commits/head-sha"):
            return _resp(method, url, {"tree": {"sha": "tree-sha"}})
        if url.endswith("/git/trees/tree-sha?recursive=1"):
            return _resp(method, url, {"truncated": False, "tree": tree})
        for sha, data in blobs.items():
            if url.endswith(f"/git/blobs/{sha}"):
                return _resp(method, url, {"encoding": "base64", "content": base64.b64encode(data).decode()})
        raise AssertionError(f"Unexpected GitHub API call: {method} {url}")

    return fake_request


@pytest.mark.asyncio
async def test_restore_brings_the_sidecars_back(monkeypatch, tmp_path):
    days = json.dumps({"days": [{"id": "d_1", "date": "2026-07-03", "title": "在一起",
                                 "yearly": True, "count": True}]}, ensure_ascii=False).encode()
    shelf = json.dumps({"items": [{"id": "s_1", "kind": "book", "title": "2666"}]},
                       ensure_ascii=False).encode()
    sync = GitHubSync(token="t", repo="owner/repo", path_prefix="ombre")
    monkeypatch.setattr(sync, "_request", _fake_github({".days.json": days, ".shelf.json": shelf}))

    result = await sync.import_from_github(str(tmp_path))

    assert result["ok"] is True and result["imported"] == 2 and result["buckets_imported"] == 0
    assert json.loads((tmp_path / ".days.json").read_text(encoding="utf-8"))["days"][0]["title"] == "在一起"
    assert (tmp_path / ".shelf.json").read_bytes() == shelf


@pytest.mark.asyncio
async def test_restore_refuses_a_broken_sidecar_before_writing(monkeypatch, tmp_path):
    (tmp_path / ".days.json").write_text('{"days": ["原来的"]}', encoding="utf-8")
    sync = GitHubSync(token="t", repo="owner/repo", path_prefix="ombre")
    monkeypatch.setattr(sync, "_request", _fake_github({".days.json": b"[1, 2, 3]"}))

    result = await sync.import_from_github(str(tmp_path))

    assert result["ok"] is False
    assert (tmp_path / ".days.json").read_text(encoding="utf-8") == '{"days": ["原来的"]}'
