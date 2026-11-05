"""Frozen display behavior and a fresh-process renderer-independent boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_feishu_card.display_redaction import redact_diagnostic_text, redact_tool_detail
from hermes_feishu_card.process_activity import process_activity
from hermes_feishu_card.render import _sanitize_runtime_header, mask_approval_scope

BASELINE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "display_redaction_48b82d9.json").read_text()
)


@pytest.mark.parametrize("case", BASELINE["redaction"], ids=[f"detail-{i}" for i in range(len(BASELINE["redaction"]))])
def test_redaction_matches_frozen_structured_and_raw_display_contract(case):
    assert redact_tool_detail(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", BASELINE["header"], ids=[f"header-{i}" for i in range(len(BASELINE["header"]))])
def test_runtime_header_redaction_and_clipping_match_frozen_contract(case):
    assert _sanitize_runtime_header(case["input"], max_chars=120) == case["expected"]


@pytest.mark.parametrize("case", BASELINE["approval"], ids=[f"approval-{i}" for i in range(len(BASELINE["approval"]))])
def test_approval_scope_redaction_matches_frozen_contract(case):
    assert mask_approval_scope(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", BASELINE["process"], ids=[f"process-{i}" for i in range(len(BASELINE["process"]))])
def test_process_text_redacts_before_head_and_tail_clip(case):
    actual = process_activity("terminal", case["args"], case["result"])
    assert actual == case["expected"]
    assert len(actual.get("command", "")) <= 600
    assert len(actual.get("output", "")) <= 600
    assert "synthetic-cross-head" not in actual.get("command", "")
    assert "synthetic-cross-tail" not in actual.get("output", "")


def test_fresh_process_activity_does_not_load_renderer_or_session(tmp_path):
    import hermes_feishu_card.process_activity as activity

    root = Path(activity.__file__).resolve().parent.parent
    code = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from hermes_feishu_card.process_activity import process_activity, enrich_process_detail
assert pathlib.Path(sys.modules['hermes_feishu_card.process_activity'].__file__).resolve().parent.parent == pathlib.Path(sys.argv[1])
result = process_activity('terminal', {'command': 'job --token synthetic-child-secret'},
                          {'session_id': 'proc_123456abcdef', 'output': 'password=synthetic-child-password'})
enrich_process_detail({'process_activity': {'id': 'proc_123456abcdef', 'action': 'log',
    'command': 'job --password synthetic-child-password', 'output': 'token=synthetic-child-secret'}}, {})
assert 'synthetic-child-secret' not in json.dumps(result)
assert 'synthetic-child-password' not in json.dumps(result)
assert 'hermes_feishu_card.render' not in sys.modules
assert 'hermes_feishu_card.session' not in sys.modules
assert 'hermes_feishu_card.display_redaction' in sys.modules
assert 'hermes_feishu_card.text' in sys.modules
print('renderer/session absent; shared redaction and text loaded')
"""
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               HERMES_FEISHU_CARD_STATE_DIR=str(tmp_path / "state"),
               PYTHONPYCACHEPREFIX=str(tmp_path / "pycache"))
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-I", "-c", code, str(root)], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "renderer/session absent" in result.stdout


@pytest.mark.parametrize(
    "text, canary",
    [
        ("api_key=SYNTHETIC_API_CANARY", "SYNTHETIC_API_CANARY"),
        ("API_KEY: SYNTHETIC_API_CANARY", "SYNTHETIC_API_CANARY"),
        ("Authorization: Bearer SYNTHETIC_AUTH_CANARY", "SYNTHETIC_AUTH_CANARY"),
        ("Authorization: Negotiate SYNTHETIC_NEGOTIATE_CANARY", "SYNTHETIC_NEGOTIATE_CANARY"),
        ("Authorization=Basic SYNTHETIC_BASIC_CANARY", "SYNTHETIC_BASIC_CANARY"),
        ('password: "first SYNTHETIC_QUOTED_CANARY"', "SYNTHETIC_QUOTED_CANARY"),
        ("token: 'first SYNTHETIC_SINGLE_CANARY'", "SYNTHETIC_SINGLE_CANARY"),
        ("https://user:SYNTHETIC_URL_CANARY@api.example.test/path", "SYNTHETIC_URL_CANARY"),
        ("Bearer SYNTHETIC_STANDALONE_CANARY", "SYNTHETIC_STANDALONE_CANARY"),
        ('{"api_key": "SYNTHETIC_JSON_CANARY"}', "SYNTHETIC_JSON_CANARY"),
    ],
)
def test_diagnostic_redaction_covers_credential_shapes(text, canary):
    redacted = redact_diagnostic_text(text)

    assert canary not in redacted
    assert "[REDACTED]" in redacted
