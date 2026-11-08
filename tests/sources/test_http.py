import json

import httpx
import pytest

from fightdb.sources import http


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    for fn in (http.download, http.get_json):
        monkeypatch.setattr(fn.retry, "sleep", lambda _s: None)  # type: ignore[attr-defined]


def _client(handler):
    return http.make_client(transport=httpx.MockTransport(handler))


def test_download_saves_file_and_etag(tmp_path):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=b"hello", headers={"etag": '"v1"'})

    dest = tmp_path / "a.bin"
    with _client(handler) as c:
        meta = http.download(c, "https://example.test/a.bin", dest)
    assert dest.read_bytes() == b"hello"
    assert meta["changed"] == "true" and meta["etag"] == '"v1"'
    saved = json.loads((tmp_path / "a.bin.meta.json").read_text(encoding="utf-8"))
    assert saved["etag"] == '"v1"'
    assert seen[0].headers["user-agent"]


def test_not_modified_keeps_file(tmp_path):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.headers.get("if-none-match"))
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=b"first", headers={"etag": '"v1"'})

    dest = tmp_path / "a.bin"
    with _client(handler) as c:
        http.download(c, "https://example.test/a.bin", dest)
        meta = http.download(c, "https://example.test/a.bin", dest)
    assert calls == [None, '"v1"']
    assert meta["changed"] == "false"
    assert dest.read_bytes() == b"first"


def test_retries_server_errors(tmp_path):
    responses = iter([httpx.Response(500), httpx.Response(200, content=b"ok")])

    with _client(lambda req: next(responses)) as c:
        http.download(c, "https://example.test/a.bin", tmp_path / "a.bin")
    assert (tmp_path / "a.bin").read_bytes() == b"ok"


def test_get_json_gives_up_after_retries():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with _client(handler) as c, pytest.raises(httpx.HTTPStatusError):
        http.get_json(c, "https://example.test/x.json")
    assert len(calls) == 4


def test_latest_release_tag_from_redirect():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "https://github.com/o/r/releases/tag/db-2026-11-01"}
        )

    with _client(handler) as c:
        assert http.latest_release_tag(c, "o/r") == "db-2026-11-01"
    assert http.release_asset_url("o/r", "x.db") == (
        "https://github.com/o/r/releases/latest/download/x.db"
    )
