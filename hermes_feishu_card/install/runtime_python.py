"""Read the same committed dependency selection as Hermes, in a child process."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


class PMRuntimeRefused(ValueError):
    """A PM checkout was found but its selected runtime cannot be verified."""


_PM_RUNTIME_PROBE = (
    "import sys,json\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from pathlib import Path\n"
    "from pm.environments import committed_venv\n"
    "venv = committed_venv(Path(sys.argv[1]))\n"
    "print(json.dumps({'venv': str(venv) if venv is not None else None}))\n"
)
_PM_RUNTIME_PROBE_TIMEOUT_SECONDS = 30.0


def pm_managed_runtime_python(
    root: Path, *, hermes_home: Path | None = None,
) -> Path | None:
    """None means no PM selection; broken selections must never choose a stale venv."""
    if not (root / "pm" / "environments.py").is_file():
        return None
    env = None
    if hermes_home is not None:
        env = dict(os.environ, HERMES_HOME=str(hermes_home))
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _PM_RUNTIME_PROBE, str(root)],
            capture_output=True, text=True, check=False,
            timeout=_PM_RUNTIME_PROBE_TIMEOUT_SECONDS, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PMRuntimeRefused("Hermes PM runtime selection probe failed") from exc
    if result.returncode != 0:
        raise PMRuntimeRefused("Hermes PM runtime selection probe failed")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        if not isinstance(payload, dict) or set(payload) != {"venv"}:
            raise ValueError("invalid selection")
        value = payload["venv"]
        if value is None:
            return None
        if not isinstance(value, str) or not value or "\x00" in value or not Path(value).is_absolute():
            raise ValueError("invalid path")
    except (ValueError, IndexError, TypeError) as exc:
        raise PMRuntimeRefused("Hermes PM runtime selection is invalid") from exc
    venv = Path(value)
    for candidate in (venv / "bin" / "python", venv / "bin" / "python3",
                      venv / "Scripts" / "python.exe"):
        try:
            if candidate.is_file():
                # Resolve the directory, not the executable symlink: Python must
                # retain the selected venv prefix, rather than use its base runtime.
                return candidate.parent.resolve(strict=True) / candidate.name
        except (OSError, RuntimeError):
            continue
    raise PMRuntimeRefused("Hermes PM selected runtime Python is missing")
