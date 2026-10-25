from hermes_feishu_card import __version__
from pathlib import Path
import re
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by Python 3.9/3.10 CI
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_package_has_version():
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", __version__)


def test_current_release_markers_are_consistent():
    expected = __version__
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    config = (ROOT / "config.yaml.example").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.example.yml").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")

    assert expected == "5.0.8"
    assert f'version = "{expected}"' in pyproject
    assert f"V{expected} sidecar configuration" in config.splitlines()[0]
    assert f'HFC_VERSION: "${{HFC_VERSION:-v{expected}}}"' in compose
    assert f"HFC_VERSION: v{expected}" in workflow


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


def test_test_extra_declares_tomli_for_python_before_311():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "tomli>=1.1.0; python_version < '3.11'" in pyproject["project"][
        "optional-dependencies"
    ]["test"]


def test_declares_exact_hermes_plugin_entry_point():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["entry-points"]["hermes_agent.plugins"] == {
        "hermes-feishu-card": "hermes_feishu_card.hermes_plugin"
    }


def test_native_hook_provenance_is_packaged_with_regular_wheels():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert package_data[
        "hermes_feishu_card.install._native_hook_provenance"
    ] == ["provenance.json", "slices/*.py"]
    resource_root = (
        ROOT / "hermes_feishu_card" / "install" / "_native_hook_provenance"
    )
    assert (resource_root / "provenance.json").is_file()
    assert len(tuple((resource_root / "slices").glob("*.py"))) == 24
