from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from hermes_feishu_card.install.detect import detect_hermes
from hermes_feishu_card.install.patcher import PATCH_BEGIN, PATCH_END


FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "hermes_v2026_4_23"
)


def test_detect_hermes_supports_v2026_4_23_fixture():
    result = detect_hermes(FIXTURE_ROOT)

    assert result.root == FIXTURE_ROOT
    assert result.version == "v2026.4.23"
    assert result.version_source == "VERSION"
    assert result.minimum_version == "v2026.4.23"
    assert result.run_py_exists is True
    assert result.run_py.name == "run.py"
    assert result.supported is True
    assert result.reason == "supported"


def test_detect_hermes_supports_git_tag_when_version_file_missing(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required for git tag fallback detection")
    _write_hermes_root(tmp_path, version=None)
    _git(tmp_path, "init")
    _git(tmp_path, "add", ".")
    _git(
        tmp_path,
        "-c",
        "user.name=Hermes Test",
        "-c",
        "user.email=hermes-test@example.com",
        "commit",
        "-m",
        "fixture",
    )
    _git(tmp_path, "tag", "v2026.4.23")

    result = detect_hermes(tmp_path)

    assert result.supported is True
    assert result.version == "v2026.4.23"
    assert result.version_source == "git tag"
    assert result.run_py_exists is True


def test_detect_hermes_rejects_parent_git_tag_when_version_file_missing(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required for git tag fallback detection")
    hermes_root = tmp_path / "nested-hermes"
    _write_hermes_root(hermes_root, version=None)
    _git(tmp_path, "init")
    _git(tmp_path, "add", ".")
    _git(
        tmp_path,
        "-c",
        "user.name=Hermes Test",
        "-c",
        "user.email=hermes-test@example.com",
        "commit",
        "-m",
        "parent fixture",
    )
    _git(tmp_path, "tag", "v2026.4.23")

    result = detect_hermes(hermes_root)

    assert result.supported is False
    assert result.version == "unknown"
    assert result.version_source == "unknown"
    assert "version" in result.reason.lower()


def test_detect_hermes_accepts_self_hooks_emit_inside_handler(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "class Gateway:\n"
            "    async def _handle_message_with_agent(self, message):\n"
            "        self.hooks.emit(\"agent:end\", {\"message\": message})\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is True


def test_detect_hermes_accepts_direct_hooks_emit_inside_handler(tmp_path):
    _write_hermes_root(tmp_path)

    result = detect_hermes(tmp_path)

    assert result.supported is True


def test_detect_hermes_rejects_missing_gateway_run_py(tmp_path):
    (tmp_path / "VERSION").write_text("v2026.4.23\n", encoding="utf-8")

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert result.run_py_exists is False
    assert result.version_source == "VERSION"
    assert "gateway/run.py" in result.reason
    assert "missing" in result.reason.lower()


def test_detect_hermes_rejects_symlinked_gateway_run_py(tmp_path):
    target = tmp_path / "target_run.py"
    target.write_text(_supported_run_py(), encoding="utf-8")
    gateway = tmp_path / "gateway"
    gateway.mkdir()
    (tmp_path / "VERSION").write_text("v2026.4.23\n", encoding="utf-8")
    (gateway / "run.py").symlink_to(target)

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "symlink" in result.reason.lower()


def test_detect_hermes_rejects_missing_required_anchor(tmp_path):
    gateway = tmp_path / "gateway"
    gateway.mkdir()
    (tmp_path / "VERSION").write_text("v2026.4.23\n", encoding="utf-8")
    (gateway / "run.py").write_text(
        "async def _handle_message_with_agent(message, hooks):\n"
        "    return message\n",
        encoding="utf-8",
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()
    assert 'hooks.emit("agent:end"' in result.reason


def test_detect_hermes_rejects_versions_below_minimum(tmp_path):
    _write_hermes_root(tmp_path, version="v2026.4.22")

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "v2026.4.23" in result.reason


def test_detect_hermes_uses_numeric_version_comparison(tmp_path):
    _write_hermes_root(tmp_path, version="v2026.10.1")

    result = detect_hermes(tmp_path)

    assert result.supported is True


def test_detect_hermes_version_components_are_semantic_not_calendar_bounds(tmp_path):
    _write_hermes_root(tmp_path, version="v2026.99.99")

    result = detect_hermes(tmp_path)

    assert result.supported is True


def test_detect_hermes_rejects_unknown_or_bad_version(tmp_path):
    _write_hermes_root(tmp_path, version=None)

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "version" in result.reason.lower()


def test_detect_hermes_rejects_comment_or_unrelated_anchor_matches(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "# async def _handle_message_with_agent(message, hooks):\n"
            "#     hooks.emit(\"agent:end\", {})\n"
            "async def helper(hooks):\n"
            "    hooks.emit(\"agent:end\", {})\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()


def test_detect_hermes_rejects_handler_nested_inside_function(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "def outer():\n"
            "    async def _handle_message_with_agent(message, hooks):\n"
            "        hooks.emit(\"agent:end\", {\"message\": message})\n"
            "    return _handle_message_with_agent\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor function" in result.reason.lower()


def test_detect_hermes_rejects_handler_in_class_nested_inside_function(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "def outer():\n"
            "    class Gateway:\n"
            "        async def _handle_message_with_agent(self, message):\n"
            "            self.hooks.emit(\"agent:end\", {\"message\": message})\n"
            "    return Gateway\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor function" in result.reason.lower()


def test_detect_hermes_rejects_anchor_only_in_nested_async_function(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "async def _handle_message_with_agent(message, hooks):\n"
            "    async def nested():\n"
            "        hooks.emit(\"agent:end\", {\"message\": message})\n"
            "    return nested\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()


def test_detect_hermes_rejects_anchor_only_in_nested_class_method(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "async def _handle_message_with_agent(message, hooks):\n"
            "    class Nested:\n"
            "        def emit_later(self):\n"
            "            hooks.emit(\"agent:end\", {\"message\": message})\n"
            "    return Nested\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()


def test_detect_hermes_rejects_anchor_only_in_lambda(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "async def _handle_message_with_agent(message, hooks):\n"
            "    emit_later = lambda: hooks.emit(\"agent:end\", {\"message\": message})\n"
            "    return emit_later\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()


def test_detect_hermes_rejects_anchor_after_return(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "async def _handle_message_with_agent(message, hooks):\n"
            "    return message\n"
            "    hooks.emit(\"agent:end\", {\"message\": message})\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()


def test_detect_hermes_rejects_anchor_after_return_in_for_body(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "async def _handle_message_with_agent(message, hooks):\n"
            "    for item in [message]:\n"
            "        return item\n"
            "        hooks.emit(\"agent:end\", {\"message\": message})\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()


def test_detect_hermes_rejects_anchor_after_return_in_try_body(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "async def _handle_message_with_agent(message, hooks):\n"
            "    try:\n"
            "        return message\n"
            "        hooks.emit(\"agent:end\", {\"message\": message})\n"
            "    except Exception:\n"
            "        return None\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()


def test_detect_hermes_rejects_anchor_only_in_static_false_branch(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "async def _handle_message_with_agent(message, hooks):\n"
            "    if False:\n"
            "        hooks.emit(\"agent:end\", {\"message\": message})\n"
            "    return message\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()


def test_detect_hermes_rejects_anchor_only_in_static_false_while(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "async def _handle_message_with_agent(message, hooks):\n"
            "    while False:\n"
            "        hooks.emit(\"agent:end\", {\"message\": message})\n"
            "    return message\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "anchor" in result.reason.lower()


def test_detect_hermes_accepts_reachable_anchor_in_try_body(tmp_path):
    _write_hermes_root(
        tmp_path,
        run_py=(
            "async def _handle_message_with_agent(message, hooks):\n"
            "    try:\n"
            "        hooks.emit(\"agent:end\", {\"message\": message})\n"
            "    except Exception:\n"
            "        return None\n"
        ),
    )

    result = detect_hermes(tmp_path)

    assert result.supported is True


def test_detect_hermes_rejects_invalid_utf8_run_py(tmp_path):
    gateway = tmp_path / "gateway"
    gateway.mkdir()
    (tmp_path / "VERSION").write_text("v2026.4.23\n", encoding="utf-8")
    (gateway / "run.py").write_bytes(b"\xff\xfe")

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "gateway/run.py" in result.reason
    assert "read" in result.reason.lower()


def test_detect_hermes_rejects_unreadable_version_directory(tmp_path):
    gateway = tmp_path / "gateway"
    gateway.mkdir()
    (tmp_path / "VERSION").mkdir()
    (gateway / "run.py").write_text(_supported_run_py(), encoding="utf-8")

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "version" in result.reason.lower()
    assert "read" in result.reason.lower()


def test_detect_hermes_rejects_invalid_utf8_version(tmp_path):
    gateway = tmp_path / "gateway"
    gateway.mkdir()
    (tmp_path / "VERSION").write_bytes(b"\xff\xfe")
    (gateway / "run.py").write_text(_supported_run_py(), encoding="utf-8")

    result = detect_hermes(tmp_path)

    assert result.supported is False
    assert "version" in result.reason.lower()
    assert "read" in result.reason.lower()


def test_patch_markers_are_stable_constants_only():
    assert PATCH_BEGIN == "# HERMES_FEISHU_CARD_PATCH_BEGIN"
    assert PATCH_END == "# HERMES_FEISHU_CARD_PATCH_END"


def _write_hermes_root(
    root: Path,
    version: str | None = "v2026.4.23",
    run_py: str | None = None,
) -> Path:
    gateway = root / "gateway"
    gateway.mkdir(parents=True)
    if version is not None:
        (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (gateway / "run.py").write_text(run_py or _supported_run_py(), encoding="utf-8")
    return root


def _supported_run_py() -> str:
    return (
        "async def _handle_message_with_agent(message, hooks):\n"
        "    hooks.emit(\"agent:end\", {\"message\": message})\n"
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
