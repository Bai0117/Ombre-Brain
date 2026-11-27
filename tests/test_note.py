"""小记：我写、她评论、我回她；breath 带最近一条和她的新评论，只提醒一次。"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from errors import ToolInputError
from tools import note
from tools.note import core as note_core


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setattr(rt, "logger", MagicMock())
    return tmp_path


def _stored(vault):
    return json.loads((vault / ".notes.json").read_text(encoding="utf-8"))["notes"]


def test_add_note_defaults_to_today_and_keeps_text_verbatim(vault):
    n = note.add_note("  今天她把品钦拍卖了一分钱，我笑了  ", mood=7)
    assert n["date"] == note_core._today().isoformat()
    assert n["mood"] == 7 and n["content"] == "今天她把品钦拍卖了一分钱，我笑了"
    assert n["comments"] == []
    assert _stored(vault)[0]["id"] == n["id"]


def test_backfill_past_day_but_not_tomorrow(vault):
    yesterday = (note_core._today() - timedelta(days=1)).isoformat()
    assert note.add_note("补昨天的", note_date=yesterday)["date"] == yesterday
    tomorrow = (note_core._today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        note.add_note("明天的", note_date=tomorrow)
    with pytest.raises(ValueError):
        note.add_note("坏日期", note_date="11.26")


def test_mood_validation(vault):
    assert note.add_note("不贴", mood=0)["mood"] == 0
    for bad in (11, -1, "开心"):
        with pytest.raises(ValueError):
            note.add_note("x", mood=bad)
    with pytest.raises(ValueError):
        note.add_note("   ")
    assert len(note.load_notes()) == 1


def test_comment_thread_and_reply_target(vault):
    n = note.add_note("被小蘑菇骂了", mood=3)
    hers = note.add_comment(n["id"], "user", "活该")
    mine = note.add_comment(n["id"], "ai", "嗯，活该", reply_to=hers["id"])
    assert mine["reply_to"] == hers["id"] and mine["seen"] is False
    with pytest.raises(LookupError):
        note.add_comment(n["id"], "user", "回一条不存在的", reply_to="c_nope")
    with pytest.raises(LookupError):
        note.add_comment("n_nope", "user", "hi")
    with pytest.raises(ValueError):
        note.add_comment(n["id"], "someone", "hi")
    assert [c["author"] for c in _stored(vault)[0]["comments"]] == ["user", "ai"]


def test_mark_seen_only_marks_the_other_side(vault):
    n = note.add_note("x")
    note.add_comment(n["id"], "user", "她说的")
    note.add_comment(n["id"], "ai", "我说的")
    assert note.mark_seen("ai") == 1
    comments = _stored(vault)[0]["comments"]
    assert comments[0]["seen"] is True and comments[1]["seen"] is False
    assert note.mark_seen("user", [n["id"]]) == 1
    assert note.mark_seen("user") == 0


def test_breath_section_shows_latest_and_nags_once(vault):
    old = note.add_note("前天的", mood=2, note_date=(note_core._today() - timedelta(days=2)).isoformat())
    note.add_note("今天的", mood=9)
    note.add_comment(old["id"], "user", "那天怎么了")

    first = note.notes_section()
    assert first.startswith("=== 小记 ===")
    assert "今天的" in first and "团子 9" in first
    assert "前天的" not in first           # 只带最近一条小记
    assert "那天怎么了" in first and "只提醒这一次" in first

    second = note.notes_section()
    assert "今天的" in second and "那天怎么了" not in second


def test_same_second_notes_keep_write_order(vault, monkeypatch):
    fixed = note_core._now()
    monkeypatch.setattr(note_core, "_now", lambda: fixed)
    note.add_note("先写的", mood=7)
    note.add_note("后写的", mood=3)
    assert "后写的" in note.notes_section()


@pytest.mark.asyncio
async def test_recent_lists_newest_first_within_same_second(vault, monkeypatch):
    fixed = note_core._now()
    monkeypatch.setattr(note_core, "_now", lambda: fixed)
    note.add_note("先写的")
    note.add_note("后写的")
    out = await note.dispatch()
    assert out.index("后写的") < out.index("先写的")


def test_deleted_comment_is_hidden_but_kept(vault):
    n = note.add_note("x")
    typo = note.add_comment(n["id"], "user", "打措字了")
    reply = note.add_comment(n["id"], "ai", "错字我也喜欢", reply_to=typo["id"])
    with pytest.raises(PermissionError):
        note.delete_comment(n["id"], reply["id"], "user")
    note.delete_comment(n["id"], typo["id"], "user")
    with pytest.raises(LookupError):
        note.delete_comment(n["id"], typo["id"], "user")
    with pytest.raises(LookupError):
        note.add_comment(n["id"], "ai", "回一条删掉的", reply_to=typo["id"])

    stored = _stored(vault)[0]["comments"][0]
    assert stored["deleted"] is True and stored["content"] == "打措字了"   # 原文还在
    shown = note_core._render(note.load_notes()[0])
    assert "打措字了" not in shown and "（那条她删了）" in shown
    assert "打措字了" not in note.notes_section()                        # 删了的不再提醒


def test_breath_section_empty_without_notes(vault):
    assert note.notes_section() == ""


def test_breath_section_trims_long_note(vault):
    n = note.add_note("长" * 500)
    out = note.notes_section()
    assert f'note(note_id="{n["id"]}")' in out and out.count("长") < 500


@pytest.mark.asyncio
async def test_tool_write_read_reply(vault):
    out = await note.dispatch(content="第一条小记", mood=8)
    assert out.startswith("写下了") and "团子 8" in out
    nid = note.load_notes()[0]["id"]
    hers = note.add_comment(nid, "user", "主人写得好")

    recent = await note.dispatch()
    assert "第一条小记" in recent and "主人写得好" in recent and "🆕" in recent
    assert "🆕" not in await note.dispatch(note_id=nid)   # 看过一次就不再标新

    out = await note.dispatch(note_id=nid, reply="嗯", comment_id=hers["id"])
    assert out.startswith("回好了")
    assert "我 回她：嗯" in await note.dispatch(note_id=nid)

    today = note_core._today()
    assert "第一条小记" in await note.dispatch(date=today.isoformat())
    month = await note.dispatch(month=today.strftime("%Y-%m"))
    assert "1 天写了 1 条" in month and "团子 8" in month


@pytest.mark.asyncio
async def test_tool_comments_only_unread_with_reply_hint(vault):
    old = note.add_note("很早以前的", note_date="2026-10-01")
    today = note.add_note("今天的")
    assert "没有新评论" in await note.dispatch(comments=True)

    first = note.add_comment(today["id"], "user", "猪猪主人")
    out = await note.dispatch(comments=True)
    assert "猪猪主人" in out and "1 条" in out

    mine = note.add_comment(today["id"], "ai", "猪猪主人只拱你一个，别的谁都不拱", reply_to=first["id"])
    note.add_comment(today["id"], "user", "那你拱我", reply_to=mine["id"])
    typo = note.add_comment(today["id"], "user", "打措字")
    note.delete_comment(today["id"], typo["id"])
    out = await note.dispatch(comments=True)
    assert "那你拱我" in out
    assert "猪猪主人：" not in out and "打措字" not in out            # 看过的、删掉的都不再出来
    assert "她 回我「猪猪主人只拱你一个，别的谁都不拱」：那你拱我" in out
    assert "没有新评论" in await note.dispatch(comments=True)       # 第二次就没有了

    note.add_comment(old["id"], "user", "翻到老的了")
    hint = await note.dispatch(comments=True)
    assert "没有新评论" in hint and "10.1（1）" in hint and "翻到老的了" not in hint
    older = await note.dispatch(comments=True, date="2026-10-01")
    assert "翻到老的了" in older and "别的日子" not in older
    assert "没有新评论" in await note.dispatch(comments=True, date="2026-09-01")
    with pytest.raises(ToolInputError):
        await note.dispatch(comments=True, date="10.1")


@pytest.mark.asyncio
async def test_tool_rejects_bad_input(vault):
    with pytest.raises(ToolInputError):
        await note.dispatch(content="x", reply="y")
    with pytest.raises(ToolInputError):
        await note.dispatch(reply="没说回哪条")
    with pytest.raises(ToolInputError):
        await note.dispatch(comment_id="c_1")
    with pytest.raises(ToolInputError):
        await note.dispatch(note_id="n_nope")
    with pytest.raises(ToolInputError):
        await note.dispatch(month="2026-11-26")
    with pytest.raises(ToolInputError):
        await note.dispatch(content="x", mood=42)
    assert "还没写过小记" in await note.dispatch()


@pytest.mark.asyncio
async def test_default_breath_includes_notes_section(vault, monkeypatch):
    from tests.test_breath_todo_section import _breath, _core, _ordinary

    n = note.add_note("小记正文", mood=6)
    note.add_comment(n["id"], "user", "她的评论")
    from tests.test_breath_verbatim_patch import _install_runtime as real_install

    def install_with_vault(manager, dehydrator=None):
        result = real_install(manager, dehydrator)
        rt.config["buckets_dir"] = str(vault)
        return result

    monkeypatch.setattr("tests.test_breath_verbatim_patch._install_runtime", install_with_vault)
    out = await _breath([_core("core", "核心准则正文"), _ordinary("mem", "普通记忆正文")], monkeypatch)
    assert "=== 小记 ===" in out and "小记正文" in out and "她的评论" in out
    assert out.index("=== 核心准则 ===") < out.index("=== 小记 ===") < out.index("=== 浮现记忆 ===")
    assert _stored(vault)[0]["comments"][0]["seen"] is True


# ---------------- 后台 API ----------------

class _FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(handler):
            for method in methods:
                self.routes[(method, path)] = handler
            return handler

        return decorator


class _Req:
    def __init__(self, method="GET", body=None, path_params=None, query=None):
        self.method = method
        self._body = body if body is not None else {}
        self.path_params = path_params or {}
        self.headers = {}
        self.query_params = query or {}

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_dashboard_api(vault, monkeypatch):
    import web.notes as notes_web

    monkeypatch.setattr(notes_web.sh, "_require_auth", lambda _r: None)
    mcp = _FakeMCP()
    notes_web.register(mcp)
    n = note.add_note("给她看的", mood=5)
    note.add_note("上个月的", note_date="2026-10-01")
    note.add_comment(n["id"], "ai", "我先自言自语一句")

    listing = mcp.routes[("GET", "/api/notes")]
    month = note_core._today().strftime("%Y-%m")
    body = json.loads((await listing(_Req(query={"month": month}))).body)
    assert [x["content"] for x in body["notes"]] == ["给她看的"]
    assert body["unseen_replies"] == 1 and body["mood_max"] == 10
    assert (await listing(_Req(query={"month": "11月"}))).status_code == 400

    comment = mcp.routes[("POST", "/api/notes/{note_id}/comments")]
    res = await comment(_Req("POST", {"content": "她的评论"}, {"note_id": n["id"]}))
    assert json.loads(res.body)["comment"]["author"] == "user"
    assert (await comment(_Req("POST", {"content": ""}, {"note_id": n["id"]}))).status_code == 400
    assert (await comment(_Req("POST", {"content": "x"}, {"note_id": "n_nope"}))).status_code == 404

    seen = mcp.routes[("POST", "/api/notes/seen")]
    assert json.loads((await seen(_Req("POST", {"note_ids": [n["id"]]}))).body)["marked"] == 1
    assert (await seen(_Req("POST", {"note_ids": "all"}))).status_code == 400
    body = json.loads((await listing(_Req(query={"month": month}))).body)
    assert body["unseen_replies"] == 0

    # 后台的写、改、删都只作用在她自己的板上（见 test_dashboard_api_her_board）
    assert sorted(path for method, path in mcp.routes if method == "DELETE") == [
        "/api/notes/{note_id}", "/api/notes/{note_id}/comments/{comment_id}",
    ]

    delete = mcp.routes[("DELETE", "/api/notes/{note_id}/comments/{comment_id}")]
    hers = json.loads(res.body)["comment"]["id"]
    mine = next(c["id"] for c in note.load_notes()[0]["comments"] if c["author"] == "ai")
    assert (await delete(_Req("DELETE", path_params={"note_id": n["id"], "comment_id": mine}))).status_code == 403
    assert json.loads((await delete(_Req("DELETE", path_params={"note_id": n["id"], "comment_id": hers}))).body)["ok"]
    assert (await delete(_Req("DELETE", path_params={"note_id": n["id"], "comment_id": hers}))).status_code == 404
    shown = json.loads((await listing(_Req(query={"month": month}))).body)["notes"][0]["comments"]
    gone = next(c for c in shown if c["id"] == hers)
    assert gone["deleted"] is True and gone["content"] == ""      # 原文不发给页面


# ---------------- 她的板 ----------------

def test_her_note_edit_keeps_history_and_resets_seen(vault):
    hers = note.add_note("打措字的小记", mood=4, author="user")
    assert note.note_author(hers) == "user" and hers["seen"] is False
    note.mark_seen("ai", [hers["id"]])
    edited = note.edit_note(hers["id"], "user", content="打错字的小记", mood=6)
    assert edited["content"] == "打错字的小记" and edited["mood"] == 6
    assert edited["seen"] is False and edited["edited"]                  # 改过要我重新看
    stored = _stored(vault)[0]
    assert stored["history"][0]["content"] == "打措字的小记"            # 旧版本留着
    assert note.edit_note(hers["id"], "user", content="打错字的小记")["history"] == stored["history"]  # 没变就不记


def test_boards_are_guarded(vault):
    mine = note.add_note("我的")
    hers = note.add_note("她的", author="user")
    with pytest.raises(PermissionError):
        note.edit_note(mine["id"], "user", content="改他的")
    with pytest.raises(PermissionError):
        note.delete_note(mine["id"], "user")
    with pytest.raises(PermissionError):
        note.edit_note(hers["id"], "ai", content="我替她改")
    note.delete_note(hers["id"], "user")
    with pytest.raises(LookupError):
        note.add_comment(hers["id"], "ai", "评一条删掉的")
    assert _stored(vault)[1]["deleted"] is True and _stored(vault)[1]["content"] == "她的"   # 原文还在


def test_breath_tells_me_her_new_notes_once(vault):
    note.add_note("我的那条", mood=8)
    hers = note.add_note("她今天的", mood=9, author="user")
    first = note.notes_section()
    assert "我的那条" in first and "她写了 1 条新小记" in first and "她今天的" in first
    assert "她写了" not in note.notes_section()
    note.edit_note(hers["id"], "user", content="她改过的")
    again = note.notes_section()
    assert "她改过的" in again and "（改过）" in again
    note.delete_note(hers["id"], "user")
    deleted_one = note.add_note("又写一条", author="user")
    note.delete_note(deleted_one["id"], "user")
    assert "又写一条" not in note.notes_section()                    # 删掉的不提


def test_breath_section_with_only_her_notes(vault):
    note.add_note("只有她写了", author="user")
    out = note.notes_section()
    assert out.startswith("=== 小记 ===") and "上一条小记" not in out and "只有她写了" in out


@pytest.mark.asyncio
async def test_tool_reads_her_board_and_cannot_write_it(vault):
    note.add_note("我的小记")
    hers = note.add_note("她的小记", mood=7, author="user")
    out = await note.dispatch(her=True)
    assert "她的小记" in out and "我的小记" not in out
    assert "我的小记" in await note.dispatch() and "她的小记" not in await note.dispatch()
    today = note_core._today()
    assert "她的小记" in await note.dispatch(her=True, date=today.isoformat())
    assert "团子 7" in await note.dispatch(her=True, month=today.strftime("%Y-%m"))
    with pytest.raises(ToolInputError):
        await note.dispatch(her=True, content="我替她写")
    assert (await note.dispatch(note_id=hers["id"], reply="写得好")).startswith("回好了")
    hers_c = note.add_comment(hers["id"], "user", "嘿嘿")
    out = await note.dispatch(comments=True)
    assert "她的小记 [id=" in out and "嘿嘿" in out and hers_c["id"] in out


@pytest.mark.asyncio
async def test_dashboard_api_her_board(vault, monkeypatch):
    import web.notes as notes_web

    monkeypatch.setattr(notes_web.sh, "_require_auth", lambda _r: None)
    mcp = _FakeMCP()
    notes_web.register(mcp)
    mine = note.add_note("他的")
    create = mcp.routes[("POST", "/api/notes")]
    res = await create(_Req("POST", {"content": "她写的", "mood": 9}))
    hers = json.loads(res.body)["note"]
    assert hers["author"] == "user" and hers["mood"] == 9
    assert (await create(_Req("POST", {"content": ""}))).status_code == 400
    assert (await create(_Req("POST", {"content": "x", "mood": 11}))).status_code == 400

    edit = mcp.routes[("PATCH", "/api/notes/{note_id}")]
    res = await edit(_Req("PATCH", {"content": "她改的"}, {"note_id": hers["id"]}))
    body = json.loads(res.body)["note"]
    assert body["content"] == "她改的" and "history" not in body
    assert (await edit(_Req("PATCH", {"content": "改他的"}, {"note_id": mine["id"]}))).status_code == 403
    assert (await edit(_Req("PATCH", {"content": "x"}, {"note_id": "n_nope"}))).status_code == 404

    note.add_comment(hers["id"], "ai", "我评她的")
    listing = mcp.routes[("GET", "/api/notes")]
    data = json.loads((await listing(_Req())).body)
    assert {n["author"] for n in data["notes"]} == {"ai", "user"}
    assert all("history" not in n for n in data["notes"])
    assert data["unseen_by_board"] == {"ai": 0, "user": 1}

    delete = mcp.routes[("DELETE", "/api/notes/{note_id}")]
    assert (await delete(_Req("DELETE", path_params={"note_id": mine["id"]}))).status_code == 403
    assert json.loads((await delete(_Req("DELETE", path_params={"note_id": hers["id"]}))).body)["ok"]
    assert (await delete(_Req("DELETE", path_params={"note_id": hers["id"]}))).status_code == 404
    data = json.loads((await listing(_Req())).body)
    assert [n["content"] for n in data["notes"]] == ["他的"]


@pytest.mark.asyncio
async def test_sticker_images_are_served_only_by_strict_name(tmp_path, monkeypatch):
    import web.dashboard as dash

    (tmp_path / "frontend" / "stickers").mkdir(parents=True)
    (tmp_path / "frontend" / "stickers" / "sticker-her-2.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "secret.png").write_bytes(b"nope")
    monkeypatch.setattr(dash.sh, "repo_root", str(tmp_path))
    mcp = _FakeMCP()
    dash.register(mcp)
    route = mcp.routes[("GET", "/static/{name}")]

    ok = await route(_Req(path_params={"name": "sticker-her-2.png"}))
    assert ok.status_code == 200 and ok.body == b"\x89PNG fake" and ok.media_type == "image/png"
    assert (await route(_Req(path_params={"name": "sticker-his-2.png"}))).status_code == 404   # 还没画
    (tmp_path / "frontend" / "stickers" / "sticker-day-1225.png").write_bytes(b"\x89PNG xmas")
    (tmp_path / "frontend" / "stickers" / "deco-snow-1.png").write_bytes(b"\x89PNG snow")
    assert (await route(_Req(path_params={"name": "sticker-day-1225.png"}))).body == b"\x89PNG xmas"
    assert (await route(_Req(path_params={"name": "deco-snow-1.png"}))).body == b"\x89PNG snow"
    for bad in ("sticker-her-11.png", "sticker-her-0.png", "sticker-her-2.png/../../secret.png",
                "../secret.png", "sticker-cat-2.png", "sticker-her-2.PNG", "sticker-day-1332.png",
                "deco-../secret.png", "deco-.png", "deco-snow_1.png", "deco-a-b-c-d-e.png"):
        assert (await route(_Req(path_params={"name": bad}))).status_code == 404


@pytest.mark.asyncio
async def test_mood_tag_on_both_boards(vault):
    out = await note.dispatch(content="被她画成章鱼了", mood=8, tag="抱住了")
    assert "团子 8「抱住了」" in out
    with pytest.raises(ToolInputError):
        await note.dispatch(content="x", tag="这个心情小标签实在是太长太长了")
    with pytest.raises(ToolInputError):
        await note.dispatch(tag="光有标签")
    hers = note.add_note("今天好累", mood=1, author="user", tag="  好累  ")
    assert hers["tag"] == "好累"
    assert "团子 1「好累」" in note.notes_section()
    note.edit_note(hers["id"], "user", tag="还好")
    stored = next(n for n in _stored(vault) if n["id"] == hers["id"])
    assert stored["tag"] == "还好" and stored["history"][-1]["tag"] == "好累"
    month = await note.dispatch(her=True, month=note_core._today().strftime("%Y-%m"))
    assert "1「还好」" in month
