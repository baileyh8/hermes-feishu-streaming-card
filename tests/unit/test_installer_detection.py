from pathlib import Path

from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.patcher import PATCH_BEGIN, PATCH_END


FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"
)


def test_detect_hermes_supports_v2026_4_23_fixture():
    result = detect_hermes(FIXTURE_ROOT)

    assert result.root == FIXTURE_ROOT
    assert result.version == "v2026.4.23"
    assert result.run_py == FIXTURE_ROOT / "gateway" / "run.py"
    assert result.supported is True
    assert result.reason == "supported"


def test_detect_hermes_rejects_missing_gateway_run_py(tmp_path):
    (tmp_path / "VERSION").write_text("v2026.4.23\n", encoding="utf-8")

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "gateway/run.py" in result.reason
    assert "missing" in result.reason.lower()


def test_detect_hermes_rejects_missing_required_anchor(tmp_path):
    gateway = tmp_path / "gateway"
    gateway.mkdir()
    (tmp_path / "VERSION").write_text("v2026.4.23\n", encoding="utf-8")
    (gateway / "run.py").write_text(
        "def _handle_message_with_agent(message, hooks):\n"
        "    return message\n",
        encoding="utf-8",
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()
    assert 'hooks.emit("agent:end"' in result.reason


def test_detect_hermes_rejects_versions_below_minimum(tmp_path):
    gateway = tmp_path / "gateway"
    gateway.mkdir()
    (tmp_path / "VERSION").write_text("v2026.4.22\n", encoding="utf-8")
    (gateway / "run.py").write_text(
        FIXTURE_ROOT.joinpath("gateway/run.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "v2026.4.23" in result.reason


def test_detect_hermes_uses_numeric_version_comparison(tmp_path):
    gateway = tmp_path / "gateway"
    gateway.mkdir()
    (tmp_path / "VERSION").write_text("v2026.10.1\n", encoding="utf-8")
    (gateway / "run.py").write_text(
        FIXTURE_ROOT.joinpath("gateway/run.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = detect_hermes(tmp_path)

    assert result.supported is True


def test_detect_hermes_rejects_unknown_or_bad_version(tmp_path):
    gateway = tmp_path / "gateway"
    gateway.mkdir()
    (gateway / "run.py").write_text(
        FIXTURE_ROOT.joinpath("gateway/run.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "version" in result.reason.lower()


def test_patch_markers_are_stable_constants_only():
    assert PATCH_BEGIN == "# HERMES_FEISHU_CARD_PATCH_BEGIN"
    assert PATCH_END == "# HERMES_FEISHU_CARD_PATCH_END"
