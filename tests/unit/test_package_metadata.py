from hermes_feishu_card import __version__
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_package_has_version():
    assert __version__ == "3.2.0"


def test_console_entrypoint_target_exists():
    from hermes_feishu_card.cli import main

    assert main([]) == 0


def test_pyproject_has_open_source_package_metadata():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'readme = "README.md"' in pyproject
    assert 'keywords = ["Hermes", "Feishu", "Lark", "streaming-card", "sidecar"]' in pyproject
    assert 'classifiers = [' in pyproject
    assert '"Programming Language :: Python :: 3.9"' in pyproject
    assert '"Programming Language :: Python :: 3.12"' in pyproject
    assert '[project.urls]' in pyproject
    assert 'Repository = "https://github.com/baileyh8/hermes-feishu-streaming-card"' in pyproject
    assert 'Issues = "https://github.com/baileyh8/hermes-feishu-streaming-card/issues"' in pyproject
