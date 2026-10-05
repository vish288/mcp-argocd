"""Tests for the pure helper functions in servers/_helpers.py."""

from __future__ import annotations

import pytest

from mcp_argocd.servers._helpers import (
    SECRET_KEYS,
    _load_file,
    _page,
    _parse_resource_selector,
    _scrub,
    _split_app_name,
    _terminal,
    _truncate,
)


class TestScrub:
    def test_drops_secret_keys_recursively(self):
        obj = {
            "repo": "https://git.example.com/x",
            "password": "hunter2",
            "nested": {"sshPrivateKey": "KEY", "keep": 1},
            "list": [{"bearerToken": "t", "ok": True}],
        }
        scrubbed = _scrub(obj)
        assert scrubbed == {
            "repo": "https://git.example.com/x",
            "nested": {"keep": 1},
            "list": [{"ok": True}],
        }

    def test_drops_config_and_jwt_tokens(self):
        assert _scrub({"name": "c", "config": {"bearerToken": "x"}}) == {"name": "c"}
        assert _scrub({"role": "r", "jwtTokens": [{"iat": 1}]}) == {"role": "r"}

    def test_every_secret_key_is_removed(self):
        obj = dict.fromkeys(SECRET_KEYS, "secret") | {"keep": 1}
        assert _scrub(obj) == {"keep": 1}

    def test_passthrough_scalars(self):
        assert _scrub("x") == "x"
        assert _scrub(5) == 5


class TestPage:
    def test_first_page_has_more(self):
        page, total, has_more, next_offset = _page(list(range(10)), 3, 0)
        assert page == [0, 1, 2]
        assert total == 10
        assert has_more is True
        assert next_offset == 3

    def test_last_page(self):
        page, total, has_more, next_offset = _page(list(range(5)), 10, 0)
        assert page == [0, 1, 2, 3, 4]
        assert total == 5
        assert has_more is False
        assert next_offset is None

    def test_offset_past_end(self):
        page, total, has_more, next_offset = _page([1, 2], 10, 5)
        assert page == []
        assert total == 2
        assert has_more is False
        assert next_offset is None


class TestSplitAppName:
    def test_plain_name(self):
        assert _split_app_name("guestbook") == ("guestbook", None)

    def test_namespace_slash_name(self):
        assert _split_app_name("team-a/guestbook") == ("guestbook", "team-a")

    def test_explicit_namespace_wins(self):
        assert _split_app_name("team-a/guestbook", "override") == ("team-a/guestbook", "override")

    def test_explicit_namespace_on_plain(self):
        assert _split_app_name("guestbook", "team-a") == ("guestbook", "team-a")


class TestParseResourceSelector:
    def test_full(self):
        assert _parse_resource_selector("apps:Deployment:web/default") == {
            "group": "apps",
            "kind": "Deployment",
            "name": "web",
            "namespace": "default",
        }

    def test_no_group(self):
        assert _parse_resource_selector("Pod:mypod") == {
            "group": "",
            "kind": "Pod",
            "name": "mypod",
            "namespace": None,
        }

    def test_group_no_namespace(self):
        assert _parse_resource_selector("apps:Deployment:web") == {
            "group": "apps",
            "kind": "Deployment",
            "name": "web",
            "namespace": None,
        }

    def test_invalid(self):
        with pytest.raises(ValueError, match="Invalid resource selector"):
            _parse_resource_selector("justaname")


class TestTruncate:
    def test_under_limit(self):
        assert _truncate("abc", 10) == ("abc", False)

    def test_over_limit(self):
        assert _truncate("abcdef", 3) == ("abc", True)

    def test_none(self):
        assert _truncate(None, 3) == (None, False)


class TestTerminal:
    @pytest.mark.parametrize("phase", ["Succeeded", "Failed", "Error"])
    def test_terminal(self, phase):
        assert _terminal(phase) is True

    @pytest.mark.parametrize("phase", ["Running", "Terminating", None])
    def test_not_terminal(self, phase):
        assert _terminal(phase) is False


class TestLoadFileSecurity:
    def test_rejects_traversal(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid filename"):
            _load_file(str(tmp_path), "../etc/passwd")

    def test_rejects_slash(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid filename"):
            _load_file(str(tmp_path), "sub/file.md")
