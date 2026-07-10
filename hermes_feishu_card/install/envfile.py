from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
from uuid import uuid4


HFC_ENV_KEYS = frozenset(
    {
        "HERMES_FEISHU_CARD_PROFILE_ID",
        "HERMES_FEISHU_CARD_EVENT_URL",
    }
)

_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*="
)
_UNQUOTED_VALUE_RE = re.compile(r"^[A-Za-z0-9_./:@%+,-]+$")


def update_hfc_env(path: Path, updates: dict[str, str]) -> None:
    """Atomically update only HFC-owned routing keys in a dotenv file."""
    normalized = _validate_updates(updates)
    env_path = Path(path).expanduser()
    original = _read_text_preserve_newlines(env_path) if env_path.exists() else ""
    newline = _preferred_newline(original)
    rendered = {key: f"{key}={_quote_value(value)}" for key, value in normalized.items()}
    seen: set[str] = set()
    output: list[str] = []

    for line in original.splitlines(keepends=True):
        body, line_ending = _split_line_ending(line)
        match = _ASSIGNMENT_RE.match(body)
        key = match.group(1) if match is not None else ""
        if key not in rendered:
            output.append(line)
            continue
        if key in seen:
            continue
        output.append(rendered[key] + line_ending)
        seen.add(key)

    contents = "".join(output)
    missing = [key for key in normalized if key not in seen]
    if missing and contents and not contents.endswith(("\n", "\r")):
        contents += newline
    for key in missing:
        contents += rendered[key] + newline

    env_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(env_path, contents)


def read_hfc_env(path: Path) -> dict[str, str]:
    """Read only HFC-owned routing values from a dotenv file."""
    env_path = Path(path).expanduser()
    try:
        contents = _read_text_preserve_newlines(env_path)
    except (FileNotFoundError, OSError):
        return {}
    values: dict[str, str] = {}
    for line in contents.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[7:].lstrip()
        if "=" not in text:
            continue
        key, raw_value = text.split("=", 1)
        key = key.strip()
        if key not in HFC_ENV_KEYS:
            continue
        values[key] = _parse_value(raw_value)
    return values


def _validate_updates(updates: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in updates.items():
        if key not in HFC_ENV_KEYS:
            raise ValueError(f"unsupported HFC env key: {key}")
        text = str(value)
        if "\n" in text or "\r" in text:
            raise ValueError(f"{key} must not contain newlines")
        normalized[key] = text
    return normalized


def _quote_value(value: str) -> str:
    if not value or _UNQUOTED_VALUE_RE.fullmatch(value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _parse_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    try:
        parts = shlex.split(value, posix=True)
    except ValueError:
        return value
    return parts[0] if parts else ""


def _preferred_newline(text: str) -> str:
    match = re.search(r"\r\n|\n|\r", text)
    return match.group(0) if match is not None else os.linesep


def _split_line_ending(line: str) -> tuple[str, str]:
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, ""


def _read_text_preserve_newlines(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _atomic_write_text(path: Path, contents: str) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(contents)
        temp_path.chmod(mode)
        _replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _replace(source: Path, target: Path) -> None:
    source.replace(target)
