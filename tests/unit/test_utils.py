"""Tests for hermes_feishu_card.utils — synchronous Feishu API helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_feishu_card.utils import (
    FeishuAPIError,
    _parse_dotenv_line,
    get_token,
    load_env,
    send_card,
    update_card,
)


# ---------------------------------------------------------------------------
# _parse_dotenv_line
# ---------------------------------------------------------------------------


class TestParseDotenvLine:
    def test_simple_key_value(self):
        assert _parse_dotenv_line("FOO=bar") == ("FOO", "bar")

    def test_strips_whitespace(self):
        assert _parse_dotenv_line("  FOO = bar  ") == ("FOO", "bar")

    def test_blank_line_returns_none(self):
        assert _parse_dotenv_line("") is None
        assert _parse_dotenv_line("   ") is None

    def test_comment_returns_none(self):
        assert _parse_dotenv_line("# this is a comment") is None

    def test_export_prefix(self):
        assert _parse_dotenv_line("export FOO=bar") == ("FOO", "bar")

    def test_export_with_spaces(self):
        assert _parse_dotenv_line("export  FOO=bar") == ("FOO", "bar")

    def test_single_quoted_value(self):
        assert _parse_dotenv_line("FOO='hello world'") == ("FOO", "hello world")

    def test_double_quoted_value(self):
        assert _parse_dotenv_line('FOO="hello world"') == ("FOO", "hello world")

    def test_empty_value(self):
        assert _parse_dotenv_line("FOO=") == ("FOO", "")

    def test_value_with_equals(self):
        assert _parse_dotenv_line("FOO=a=b") == ("FOO", "a=b")

    def test_no_equals_returns_none(self):
        assert _parse_dotenv_line("INVALID") is None

    def test_empty_key_returns_none(self):
        assert _parse_dotenv_line("=value") is None


# ---------------------------------------------------------------------------
# load_env
# ---------------------------------------------------------------------------


class TestLoadEnv:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_env(tmp_path / "nonexistent.env")
        assert result == {}

    def test_reads_key_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "FEISHU_APP_ID=cli_test123\n"
            "FEISHU_APP_SECRET=secret456\n"
            "# comment\n"
            "OTHER=value\n"
        )
        result = load_env(env_file)
        assert result == {
            "FEISHU_APP_ID": "cli_test123",
            "FEISHU_APP_SECRET": "secret456",
            "OTHER": "value",
        }

    def test_handles_export_prefix(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("export FEISHU_APP_ID=cli_export\n")
        result = load_env(env_file)
        assert result["FEISHU_APP_ID"] == "cli_export"


# ---------------------------------------------------------------------------
# get_token
# ---------------------------------------------------------------------------


class TestGetToken:
    def test_explicit_credentials(self, tmp_path):
        """Passing app_id/app_secret directly should work without .env file."""
        import hermes_feishu_card.utils as utils_mod

        # Reset token cache
        utils_mod._token_cache = None
        utils_mod._token_expires_at = 0

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "code": 0,
            "msg": "ok",
            "tenant_access_token": "t-test-token",
            "expire": 7200,
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("hermes_feishu_card.utils.urlopen", return_value=mock_response):
            token = get_token(
                app_id="cli_test",
                app_secret="secret_test",
            )
        assert token == "t-test-token"

    def test_raises_when_credentials_missing(self, tmp_path):
        """Should raise FeishuAPIError when no credentials found."""
        import hermes_feishu_card.utils as utils_mod

        utils_mod._token_cache = None
        utils_mod._token_expires_at = 0

        with pytest.raises(FeishuAPIError, match="Missing FEISHU_APP_ID"):
            get_token(env_path=tmp_path / "empty.env")

    def test_caches_token(self):
        """Second call should return cached token without HTTP request."""
        import hermes_feishu_card.utils as utils_mod

        utils_mod._token_cache = "cached-token"
        utils_mod._token_expires_at = 9999999999.0

        token = get_token(app_id="x", app_secret="y")
        assert token == "cached-token"


# ---------------------------------------------------------------------------
# send_card
# ---------------------------------------------------------------------------


class TestSendCard:
    def test_returns_message_id(self):
        import hermes_feishu_card.utils as utils_mod

        utils_mod._token_cache = "test-token"
        utils_mod._token_expires_at = 9999999999.0

        card = {
            "header": {"title": {"tag": "plain_text", "content": "Test"}},
            "elements": [{"tag": "markdown", "content": "Hello"}],
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "code": 0,
            "data": {"message_id": "om_test_msg_123"},
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("hermes_feishu_card.utils.urlopen", return_value=mock_response) as mock_open:
            msg_id = send_card("oc_test_chat", card)

        assert msg_id == "om_test_msg_123"
        # Verify the request was made
        mock_open.assert_called_once()

    def test_raises_on_api_error(self):
        import hermes_feishu_card.utils as utils_mod

        utils_mod._token_cache = "test-token"
        utils_mod._token_expires_at = 9999999999.0

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "code": 99991,
            "msg": "chat not found",
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("hermes_feishu_card.utils.urlopen", return_value=mock_response):
            with pytest.raises(FeishuAPIError, match="99991"):
                send_card("oc_bad_chat", {"elements": []})


# ---------------------------------------------------------------------------
# update_card
# ---------------------------------------------------------------------------


class TestUpdateCard:
    def test_update_sends_patch(self):
        import hermes_feishu_card.utils as utils_mod

        utils_mod._token_cache = "test-token"
        utils_mod._token_expires_at = 9999999999.0

        card = {
            "header": {"title": {"tag": "plain_text", "content": "Updated"}},
            "elements": [{"tag": "markdown", "content": "Done"}],
        }

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"code": 0}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("hermes_feishu_card.utils.urlopen", return_value=mock_response) as mock_open:
            update_card("om_test_msg_123", card)

        req = mock_open.call_args[0][0]
        assert req.method == "PATCH"
        assert "om_test_msg_123" in req.full_url
