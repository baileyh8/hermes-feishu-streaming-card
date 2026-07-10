from pathlib import Path

import pytest

from hermes_feishu_card.install import envfile
from hermes_feishu_card.install.envfile import read_hfc_env, update_hfc_env


def test_update_hfc_env_preserves_comments_unknown_keys_and_order(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# local browser\n"
        "AGENT_BROWSER_PATH=/Applications/Chrome\n"
        "HERMES_FEISHU_CARD_PROFILE_ID=old\n",
        encoding="utf-8",
    )

    update_hfc_env(
        env_path,
        {
            "HERMES_FEISHU_CARD_PROFILE_ID": "child",
            "HERMES_FEISHU_CARD_EVENT_URL": "http://127.0.0.1:8766/events",
        },
    )

    text = env_path.read_text(encoding="utf-8")
    assert text == (
        "# local browser\n"
        "AGENT_BROWSER_PATH=/Applications/Chrome\n"
        "HERMES_FEISHU_CARD_PROFILE_ID=child\n"
        "HERMES_FEISHU_CARD_EVENT_URL=http://127.0.0.1:8766/events\n"
    )


def test_update_hfc_env_preserves_crlf_and_removes_owned_duplicates(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_bytes(
        b"# Windows\r\n"
        b"HERMES_FEISHU_CARD_PROFILE_ID=old\r\n"
        b"UNKNOWN=keep\r\n"
        b"export HERMES_FEISHU_CARD_PROFILE_ID=duplicate\r\n"
    )

    update_hfc_env(env_path, {"HERMES_FEISHU_CARD_PROFILE_ID": "child"})

    assert env_path.read_bytes() == (
        b"# Windows\r\n"
        b"HERMES_FEISHU_CARD_PROFILE_ID=child\r\n"
        b"UNKNOWN=keep\r\n"
    )


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        ("", ""),
        ("child profile", "'child profile'"),
        ("child's profile", "'child'\"'\"'s profile'"),
    ],
)
def test_update_hfc_env_quotes_values_for_dotenv(value, rendered, tmp_path):
    env_path = tmp_path / ".env"

    update_hfc_env(env_path, {"HERMES_FEISHU_CARD_PROFILE_ID": value})

    assert env_path.read_text(encoding="utf-8") == (
        f"HERMES_FEISHU_CARD_PROFILE_ID={rendered}\n"
    )


def test_update_hfc_env_rejects_unknown_key_without_creating_file(tmp_path):
    env_path = tmp_path / ".env"

    with pytest.raises(ValueError, match="unsupported HFC env key"):
        update_hfc_env(env_path, {"FEISHU_APP_SECRET": "do-not-touch"})

    assert not env_path.exists()


@pytest.mark.parametrize("value", ["child\nINJECTED=1", "child\rINJECTED=1"])
def test_update_hfc_env_rejects_newline_injection(value, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("UNKNOWN=keep\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must not contain newlines"):
        update_hfc_env(env_path, {"HERMES_FEISHU_CARD_PROFILE_ID": value})

    assert env_path.read_text(encoding="utf-8") == "UNKNOWN=keep\n"


def test_update_hfc_env_atomic_replace_failure_preserves_original(
    monkeypatch, tmp_path
):
    env_path = tmp_path / ".env"
    original = b"# keep\nHERMES_FEISHU_CARD_PROFILE_ID=old\n"
    env_path.write_bytes(original)

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(envfile, "_replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        update_hfc_env(env_path, {"HERMES_FEISHU_CARD_PROFILE_ID": "child"})

    assert env_path.read_bytes() == original
    assert list(tmp_path.glob(".*.tmp")) == []


def test_read_hfc_env_parses_only_owned_keys(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "UNKNOWN=do-not-source\n"
        "export HERMES_FEISHU_CARD_PROFILE_ID='child'\n"
        'HERMES_FEISHU_CARD_EVENT_URL="http://sidecar:8765/events"\n',
        encoding="utf-8",
    )

    assert read_hfc_env(env_path) == {
        "HERMES_FEISHU_CARD_PROFILE_ID": "child",
        "HERMES_FEISHU_CARD_EVENT_URL": "http://sidecar:8765/events",
    }


def test_update_hfc_env_preserves_private_file_mode(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "FEISHU_APP_SECRET=keep-private\n"
        "HERMES_FEISHU_CARD_PROFILE_ID=old\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)

    update_hfc_env(env_path, {"HERMES_FEISHU_CARD_PROFILE_ID": "child"})

    assert env_path.stat().st_mode & 0o777 == 0o600
