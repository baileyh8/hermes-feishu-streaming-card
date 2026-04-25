from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


MIN_SUPPORTED_VERSION = "v2026.4.23"
REQUIRED_ANCHORS = (
    "_handle_message_with_agent",
    'hooks.emit("agent:end"',
)
_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class HermesDetection:
    root: Path
    version: str
    run_py: Path
    supported: bool
    reason: str


def detect_hermes(root: str | Path) -> HermesDetection:
    hermes_root = Path(root)
    run_py = hermes_root / "gateway" / "run.py"
    version = _read_version(hermes_root / "VERSION")

    if not run_py.exists():
        return HermesDetection(
            root=hermes_root,
            version=version,
            run_py=run_py,
            supported=False,
            reason="gateway/run.py missing",
        )

    parsed_version = _parse_version(version)
    minimum_version = _parse_version(MIN_SUPPORTED_VERSION)
    if parsed_version is None:
        return HermesDetection(
            root=hermes_root,
            version=version,
            run_py=run_py,
            supported=False,
            reason="Hermes VERSION missing, unknown, or invalid",
        )
    if minimum_version is not None and parsed_version < minimum_version:
        return HermesDetection(
            root=hermes_root,
            version=version,
            run_py=run_py,
            supported=False,
            reason=f"Hermes version must be at least {MIN_SUPPORTED_VERSION}",
        )

    contents = run_py.read_text(encoding="utf-8")
    missing_anchors = [anchor for anchor in REQUIRED_ANCHORS if anchor not in contents]
    if missing_anchors:
        return HermesDetection(
            root=hermes_root,
            version=version,
            run_py=run_py,
            supported=False,
            reason=f"gateway/run.py missing anchor: {missing_anchors[0]}",
        )

    return HermesDetection(
        root=hermes_root,
        version=version,
        run_py=run_py,
        supported=True,
        reason="supported",
    )


def _read_version(path: Path) -> str:
    if not path.exists():
        return "unknown"
    return path.read_text(encoding="utf-8").strip() or "unknown"


def _parse_version(version: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(version.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())
