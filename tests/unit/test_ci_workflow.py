from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_github_actions_runs_full_pytest_matrix():
    workflow = ROOT / ".github" / "workflows" / "tests.yml"

    text = workflow.read_text(encoding="utf-8")

    assert "pull_request:" in text
    assert "push:" in text
    assert 'python-version: ["3.9", "3.12"]' in text
    assert 'python -m pip install -e ".[test]"' in text
    assert "python -m pytest -q" in text
