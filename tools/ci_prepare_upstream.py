"""Fetch pinned CI sources once and create their original detached worktrees.

The caller supplies an exclusive CI workspace. Existing targets and caches are
never reused or removed. Failed preparation retains its own partial state for
inspection; a subsequent attempt must use a fresh workspace.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

OFFICIAL_REMOTE_URL = "https://github.com/NousResearch/hermes-agent.git"
CACHE_NAME = ".hfc-source-cache"
REF_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
SAFE_PATH_PATTERN = re.compile(r"\.upstream/[A-Za-z0-9][A-Za-z0-9_-]*")
FORBIDDEN_NAMES = {"fixed", CACHE_NAME, "hermes_cache.git"}


def _metadata(path: Path) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("Cannot inspect source preparation path") from exc
    if (stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise ValueError("Source preparation path is linked")
    return metadata


def _directory(path: Path, *, required: bool = False) -> None:
    metadata = _metadata(path)
    if metadata is None:
        if required:
            raise ValueError("Source preparation workspace is missing")
    elif not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Source preparation parent is not a directory")


def validate_remote_url(url: str) -> None:
    """Only production's fixed remote or an explicit local test repository."""
    if url == OFFICIAL_REMOTE_URL:
        return
    if (not isinstance(url, str) or not url or url.startswith("-")
            or any(character in url for character in "\r\n\x00")):
        raise ValueError("Invalid source preparation remote")
    local = Path(url)
    if not local.is_absolute():
        raise ValueError("Source preparation remote is not permitted")
    _directory(local, required=True)


def validate_manifest(manifest: Any) -> list[dict[str, str]]:
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("Source manifest must be a nonempty list")
    seen_paths: set[str] = set()
    validated: list[dict[str, str]] = []
    for entry in manifest:
        if not isinstance(entry, dict) or set(entry) != {"name", "ref", "path"}:
            raise ValueError("Invalid source manifest entry")
        name, ref, path = entry["name"], entry["ref"], entry["path"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid source manifest name")
        if not isinstance(ref, str) or REF_PATTERN.fullmatch(ref) is None:
            raise ValueError("Source manifest requires a full commit ID")
        if (not isinstance(path, str) or SAFE_PATH_PATTERN.fullmatch(path) is None
                or path.split("/")[-1] in FORBIDDEN_NAMES or path in seen_paths):
            raise ValueError("Invalid or duplicate source manifest path")
        seen_paths.add(path)
        validated.append({"name": name, "ref": ref.lower(), "path": path})
    return validated


def preflight_check(manifest: Any, target_root: Path,
                    cache_dir: Path) -> list[dict[str, str]]:
    entries = validate_manifest(manifest)
    if not target_root.is_absolute():
        raise ValueError("Source preparation requires an absolute workspace")
    if cache_dir != target_root / ".upstream" / CACHE_NAME:
        raise ValueError("Source cache must use the workspace's reserved path")
    _directory(target_root, required=True)
    _directory(target_root / ".upstream")
    # Inspect all destinations before creating anything, including the cache.
    for path in [cache_dir, *(target_root / entry["path"] for entry in entries)]:
        if _metadata(path) is not None:
            raise ValueError("Source preparation destination already exists")
    return entries


def _git(arguments: list[str], *, phase: str) -> str:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("GIT_CONFIG_") and key not in {
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_NAMESPACE", "GIT_CONFIG",
        }
    }
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *arguments],
        capture_output=True, text=True, env=environment, check=False,
    )
    if result.returncode:
        # Do not echo resolver URLs, user paths or credential helpers.
        raise RuntimeError(f"Git source preparation failed during {phase} ({result.returncode})")
    return result.stdout.strip()


def prepare_upstream_worktrees(manifest_raw: Any, target_root: Path,
                               cache_dir: Path,
                               remote_url: str = OFFICIAL_REMOTE_URL) -> None:
    validate_remote_url(remote_url)
    entries = preflight_check(manifest_raw, target_root, cache_dir)
    upstream = target_root / ".upstream"
    upstream.mkdir(exist_ok=True)
    _directory(upstream, required=True)
    cache_dir.mkdir()  # Exclusive creation refuses an unexpected existing path.
    template = cache_dir / "empty-template"
    template.mkdir()
    git_dir = cache_dir / "hermes_cache.git"
    _git(["init", "--bare", f"--template={template}", str(git_dir)], phase="init")
    _git(["-C", str(git_dir), "remote", "add", "origin", remote_url], phase="init")
    unique_refs = sorted({entry["ref"] for entry in entries})
    _git(["-C", str(git_dir), "fetch", "--depth=1", remote_url, *unique_refs], phase="fetch")
    # No automatic rollback deletes paths. GitHub retires its private workspace;
    # failed local invocations retain owned partial state for diagnosis.
    for entry in entries:
        target = target_root / entry["path"]
        if _metadata(target) is not None:
            raise ValueError("Source destination changed during preparation")
        _directory(upstream, required=True)
        _git(["--git-dir", str(git_dir), "worktree", "add", "--detach",
              str(target), entry["ref"]], phase="worktree")
        _directory(target, required=True)
        head = _git(["-C", str(target), "rev-parse", "HEAD"], phase="verify")
        if head.lower() != entry["ref"]:
            raise RuntimeError("Prepared source commit differs from its pin")
        common = Path(_git(["-C", str(target), "rev-parse", "--git-common-dir"], phase="verify"))
        if not common.is_absolute():
            common = target / common
        if common.resolve(strict=True) != git_dir.resolve(strict=True):
            raise RuntimeError("Prepared source uses a different Git object store")
        top = _git(["-C", str(target), "rev-parse", "--show-toplevel"], phase="verify")
        if Path(top).resolve(strict=True) != target.resolve(strict=True):
            raise RuntimeError("Prepared source worktree root differs")
        if _git(["-C", str(target), "status", "--porcelain"], phase="verify"):
            raise RuntimeError("Prepared source worktree is dirty")
    print(f"Prepared and verified {len(entries)} pinned upstream worktrees", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    root = args.target_root.absolute()  # Preserve links until lstat checks them.
    prepare_upstream_worktrees(manifest, root, root / ".upstream" / CACHE_NAME)


if __name__ == "__main__":
    main()
