from __future__ import annotations

from pathlib import Path


def test_tools_package_source_compiles() -> None:
    source = Path("tools/__init__.py").read_text(encoding="utf-8")

    compile(source, "tools/__init__.py", "exec")
