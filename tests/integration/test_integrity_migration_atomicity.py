"""CLI migration must roll back owned metadata after late source drift."""
import json
from pathlib import Path
import shutil

from hermes_feishu_card import cli
from hermes_feishu_card.install import decomposed
from hermes_feishu_card.install.detect import detect_hermes


def test_migrate_safe_rolls_back_metadata_after_both_writes_and_preserves_user_source(
    tmp_path, monkeypatch, capsys,
):
    root = tmp_path / "hermes"
    fixture = Path(__file__).parents[1] / "fixtures" / "hermes_decomposed"
    shutil.copytree(fixture, root, ignore=shutil.ignore_patterns("__pycache__"))
    (root / "VERSION").write_text("0.21.0\n")
    decomposed.install(detect_hermes(root))
    manifest_path = root / decomposed.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("integrity")
    manifest_path.write_text(json.dumps(manifest) + "\n")
    env_path = tmp_path / ".env"
    env_path.write_text("# operator setting\nHERMES_FEISHU_CARD_INTEGRITY_MODE=notify\n")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server: {}\n")
    source = root / "gateway/run_turn.py"
    source_before = source.read_bytes()
    user_source = source_before + b"\n# concurrent operator source change\n"
    metadata_before = {path: path.read_bytes() for path in (manifest_path, env_path)}
    backups_before = {path: path.read_bytes() for path in root.rglob("*.hermes_feishu_card.bak")}
    atomic_write = cli._atomic_write_text
    writes = []
    injected = False

    def drift_after_env_commit(path, contents, **kwargs):
        nonlocal injected
        result = atomic_write(path, contents, **kwargs)
        writes.append(path)
        if path == env_path and not injected:
            # Injection happens after real manifest AND env replacements, not
            # during rendering or pre-commit validation.
            assert json.loads(manifest_path.read_text())["integrity"]["kind"] == "verified_owned_snapshot"
            assert "HERMES_FEISHU_CARD_INTEGRITY_MODE=safe" in env_path.read_text()
            injected = True
            source.write_bytes(user_source)
        return result

    monkeypatch.setattr(cli, "_atomic_write_text", drift_after_env_commit)
    result = cli.main([
        "integrity", "migrate-safe", "--config", str(config_path),
        "--hermes-dir", str(root), "--yes",
    ])

    output = capsys.readouterr()
    assert injected and result == 1
    assert "error:" in output.err
    assert "integrity migration: verified" not in output.out
    assert writes == [manifest_path, env_path, env_path, manifest_path]
    assert {path: path.read_bytes() for path in metadata_before} == metadata_before
    assert {path: path.read_bytes() for path in backups_before} == backups_before
    assert source.read_bytes() == user_source
    assert config_path.read_text() == "server: {}\n"
