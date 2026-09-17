"""Hermes extracted clarify-seam patcher contracts.

Hermes ``242ff24ff7`` (2026-09-16) moved the clarify body out of
``_clarify_callback_sync`` into ``_ask_clarify_question`` and took the
``ctx = self._ctx`` binding with it, so the legacy seam no longer locates.
"""

from pathlib import Path

import pytest

from hermes_feishu_card.install import patcher


FIXTURE = Path(__file__).parents[1] / "fixtures/hermes_extracted_clarify.py"
LEGACY_FIXTURE = (
    Path(__file__).parents[1] / "fixtures/hermes_decomposed/gateway/run_turn_runner.py"
)
TARGET = "gateway/run_turn_runner.py"


def _install(fixture: Path = FIXTURE):
    original = fixture.read_text(encoding="utf-8")
    return original, patcher.apply_gateway_fragment(original, TARGET)


def test_extracted_seam_install_is_idempotent_and_reversible() -> None:
    original, installed = _install()
    assert patcher.CLARIFY_PATCH_BEGIN in installed
    assert patcher.apply_gateway_fragment(installed, TARGET) == installed
    compile(installed, str(FIXTURE), "exec")
    assert patcher.remove_patch(installed) == original


def test_extracted_seam_hook_answers_with_the_answered_flag() -> None:
    """The helper is unpacked as ``(response, answered)``, so the hook returns both."""
    _, installed = _install()
    block = installed[
        installed.index(patcher.CLARIFY_PATCH_BEGIN) : installed.index(patcher.CLARIFY_PATCH_END)
    ]
    assert "return _hfc_clarify_response, True" in block
    assert "_hfc_turn_ctx = ctx" in block
    assert installed.count(patcher.CLARIFY_PATCH_BEGIN) == 1


def test_extracted_seam_hook_lands_inside_the_helper_not_the_callback() -> None:
    _, installed = _install()
    helper_at = installed.index("def _ask_clarify_question")
    marker_at = installed.index(patcher.CLARIFY_PATCH_BEGIN)
    next_def_at = installed.index("\n    def ", helper_at + 1)
    assert helper_at < marker_at < next_def_at


def test_extracted_seam_rejects_a_dropped_context_binding() -> None:
    original = FIXTURE.read_text(encoding="utf-8")
    drifted = original.replace(
        "        ctx = self._ctx\n        if not ctx._status_adapter:",
        "        if not self._ctx._status_adapter:",
    )
    assert drifted != original
    with pytest.raises(ValueError, match="no longer binds the TurnRunner context"):
        patcher.apply_gateway_fragment(drifted, TARGET)


def test_extracted_seam_rejects_a_renamed_argument() -> None:
    original = FIXTURE.read_text(encoding="utf-8")
    drifted = original.replace(
        "def _ask_clarify_question(self, question, choices, multi_select",
        "def _ask_clarify_question(self, question, options, multi_select",
    )
    assert drifted != original
    with pytest.raises(ValueError, match="lost choices"):
        patcher.apply_gateway_fragment(drifted, TARGET)


def test_pre_extraction_layout_keeps_the_string_returning_callback_hook() -> None:
    """The 8-file layout without the helper must not grow the tuple spelling."""
    original, installed = _install(LEGACY_FIXTURE)
    assert patcher.CLARIFY_PATCH_BEGIN in installed
    assert "return _hfc_clarify_response, True" not in installed
    assert patcher.remove_patch(installed) == original


def _call_extracted_seam(monkeypatch, answer):
    from hermes_feishu_card import hook_runtime

    original = FIXTURE.read_text(encoding="utf-8")
    namespace = {}
    exec(compile(patcher.apply_gateway_fragment(original, TARGET), str(FIXTURE), "exec"), namespace)

    class Adapter:
        def __init__(self):
            self.sent = []

        def send_clarify(self, **kwargs):
            self.sent.append(kwargs)

    class Ctx:
        _status_chat_id = "chat"
        session_key = "session"
        source = None
        event_message_id = "message"
        _loop_for_step = None

        def __init__(self):
            self._status_adapter = Adapter()

        def _run_still_current(self):
            return True

        def wait_for_clarify(self, question, choices):
            return "native-response"

    ctx = Ctx()
    runner = namespace["TurnRunner"](object(), ctx)
    monkeypatch.setattr(
        hook_runtime,
        "request_clarify_response_from_hermes_locals",
        lambda locals_map, **kwargs: answer,
    )
    result = runner._ask_clarify_question("pick one", ["a", "b"], False)
    return result, ctx._status_adapter.sent


def test_extracted_seam_hook_answers_for_the_platform(monkeypatch) -> None:
    """A Feishu answer must satisfy the tuple contract and skip the native card."""
    result, native_sends = _call_extracted_seam(monkeypatch, "feishu-answer")
    assert result == ("feishu-answer", True)
    assert native_sends == []


def test_extracted_seam_falls_through_when_the_platform_has_no_answer(monkeypatch) -> None:
    """No Feishu answer must leave Hermes' own card path intact."""
    result, native_sends = _call_extracted_seam(monkeypatch, None)
    assert result == ("native-response", True)
    assert len(native_sends) == 1
