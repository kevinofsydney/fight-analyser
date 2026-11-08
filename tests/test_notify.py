import httpx

from fightdb import notify


def test_no_url_or_no_changes_sends_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not post")

    monkeypatch.setattr(httpx, "post", boom)
    assert notify.notify_card_changes("", ["x"]) is False
    assert notify.notify_card_changes("https://discord.test/hook", []) is False


def test_posts_each_change_once(monkeypatch):
    sent = []

    def fake_post(url, json, timeout):
        sent.append((url, json))
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert notify.notify_card_changes(
        "https://discord.test/hook", ["A vs B cancelled", "C replaces D"]
    )
    assert len(sent) == 1
    body = sent[0][1]["content"]
    assert "A vs B cancelled" in body and "C replaces D" in body


def test_long_messages_are_truncated_and_errors_swallowed(monkeypatch):
    sent = []

    def fake_post(url, json, timeout):
        sent.append(json["content"])
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert notify.notify_card_changes("https://discord.test/hook", ["x" * 500] * 10) is False
    assert len(sent[0]) <= notify.DISCORD_LIMIT + 2
