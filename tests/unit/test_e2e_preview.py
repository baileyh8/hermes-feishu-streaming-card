from __future__ import annotations

import json
import subprocess
import sys


def test_generate_e2e_preview_writes_visual_and_card_json(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "tools/generate_e2e_preview.py",
            "--output-dir",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    svg = (tmp_path / "e2e-card-preview.svg").read_text(encoding="utf-8")
    cards = json.loads((tmp_path / "e2e-card-preview.json").read_text(encoding="utf-8"))

    assert "Hermes Agent" in svg
    assert "思考中" in svg
    assert "已完成" in svg
    assert "工具调用 2 次" in svg
    assert "读取资料" in svg
    assert "生成答案" in svg
    assert "</think>" not in svg
    assert set(cards) == {"thinking", "completed"}
    assert cards["thinking"]["schema"] == "2.0"
    assert cards["completed"]["header"]["subtitle"]["content"] == "已完成"
