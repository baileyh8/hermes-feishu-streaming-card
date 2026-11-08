from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import cast

TRUSTED_PROFILE_SOURCES = frozenset(
    {"env", "locals", "hermes_home", "fallback_default"}
)
SANITIZED_PROFILE_SOURCES = frozenset(
    {"sanitized_env", "sanitized_locals", "sanitized_hermes_home"}
)
PROFILE_SOURCES = TRUSTED_PROFILE_SOURCES | SANITIZED_PROFILE_SOURCES

PROFILE_SOURCE_FALLBACK = "fallback_default"
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# Canonical named-profile id grammar for root-bound recognition (#512). The
# legacy non-root contract keeps its own wider pattern above.
ROOT_PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def resolve_authenticated_profile_identity(
    *,
    explicit_env_profile: object = None,
    authenticated_session_profile: object = None,
    hermes_home_profile: object = None,
    hermes_home_membership_verified: object = False,
    no_named_profile: object = False,
) -> tuple[str, str] | None:
    """Resolve explicit authenticated profile facts without scanning objects."""
    if (
        type(hermes_home_membership_verified) is not bool
        or type(no_named_profile) is not bool
    ):
        return None
    if hermes_home_profile is None:
        if hermes_home_membership_verified:
            return None
    elif not hermes_home_membership_verified:
        return None

    candidates = (
        (explicit_env_profile, "env"),
        (authenticated_session_profile, "locals"),
        (hermes_home_profile, "hermes_home"),
    )
    facts: list[tuple[str, str]] = []
    for profile_id, source in candidates:
        if profile_id is None:
            continue
        if not _is_exact_safe_profile_id(profile_id):
            return None
        facts.append((cast(str, profile_id), source))
    if no_named_profile:
        if facts:
            return None
        return "default", PROFILE_SOURCE_FALLBACK
    if not facts:
        return None
    if any(profile_id != facts[0][0] for profile_id, _source in facts[1:]):
        return None
    return facts[0]


def validate_trusted_profile_identity(
    profile_id: object,
    profile_source: object,
    *,
    hermes_home_membership_verified: object = False,
) -> tuple[str, str] | None:
    """Validate one already-selected ingress profile and its explicit evidence."""
    if (
        not _is_exact_safe_profile_id(profile_id)
        or type(profile_source) is not str
        or profile_source not in TRUSTED_PROFILE_SOURCES
        or type(hermes_home_membership_verified) is not bool
    ):
        return None
    if profile_source == "hermes_home":
        if not hermes_home_membership_verified:
            return None
    elif hermes_home_membership_verified:
        return None
    if profile_source == PROFILE_SOURCE_FALLBACK and profile_id != "default":
        return None
    return cast(str, profile_id), profile_source


def legacy_safe_profile_id(value: str) -> str:
    candidate = value.strip()
    if PROFILE_ID_PATTERN.fullmatch(candidate):
        return candidate
    return "default"


def legacy_profile_identity(value: str, source: str) -> tuple[str, str]:
    profile_id = legacy_safe_profile_id(value)
    if profile_id == "default" and value.strip() != "default":
        return profile_id, f"sanitized_{source}"
    return profile_id, source


def profile_from_hermes_home_path(path: str, *, hermes_root: str | None = None) -> str | None:
    """Legacy ``<X>/.hermes|hermes/profiles/<name>`` parser, or root-bound.

    Without ``hermes_root`` the historical string contract is unchanged. With
    an explicit verified root only two shapes match: the root itself is the
    default profile, and ``<root>/profiles/<name>`` (exact depth) names the
    profile. No fallthrough to the legacy ``.hermes`` rule; the caller owns
    proving the root (native ``get_default_hermes_root``) before passing it.
    """
    if not path:
        return None
    if hermes_root is not None:
        root_windows = PureWindowsPath(str(hermes_root))
        # Pick one grammar per call: Windows when the root carries a drive
        # or UNC shape, POSIX otherwise. Grammar mixing is refused by the
        # absolute check on the same shape.
        windows_style = (
            root_windows.drive != ""
            or str(hermes_root).replace("\\", "/").startswith("//")
        )
        root_shape = root_windows if windows_style else PurePosixPath(str(hermes_root))
        path_shape = (
            PureWindowsPath(str(path)) if windows_style else PurePosixPath(str(path))
        )
        if not root_shape.is_absolute() or not path_shape.is_absolute():
            return None
        if ".." in root_shape.parts or ".." in path_shape.parts:
            return None
        if path_shape == root_shape:
            return "default"
        if path_shape.parent != root_shape / "profiles":
            return None
        candidate = path_shape.name
        return candidate if ROOT_PROFILE_ID_PATTERN.fullmatch(candidate) else None
    normalized = str(Path(path).expanduser()).replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part)
    for index in range(len(parts) - 2):
        if parts[index] in {".hermes", "hermes"} and parts[index + 1] == "profiles":
            if index + 3 != len(parts):
                return None
            candidate = parts[index + 2].strip()
            if candidate:
                return candidate
    return None


def _is_exact_safe_profile_id(value: object) -> bool:
    return type(value) is str and PROFILE_ID_PATTERN.fullmatch(value) is not None
