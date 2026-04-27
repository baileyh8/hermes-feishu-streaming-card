import hashlib

from hermes_feishu_card.install.manifest import file_sha256


def test_file_sha256_returns_stable_digest(tmp_path):
    path = tmp_path / "run.py"
    path.write_text("hello hermes\n", encoding="utf-8")

    assert file_sha256(path) == hashlib.sha256(b"hello hermes\n").hexdigest()
