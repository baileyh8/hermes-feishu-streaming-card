"""Shared runtime adapter for the #512 root-bound profile identity.

``hook_runtime`` and ``admission_completion`` import this module instead of
each other or of repeating resolution logic. The verified process root comes
only from native ``hermes_constants.get_default_hermes_root()``; the task home
comes from ``get_hermes_home()`` (ContextVar). A missing native helper keeps
the pre-#512 legacy behavior; a helper that fails, resolves to a relative or
unprovable root refuses (fail closed) -- missing capability and failed
inspection are distinct outcomes.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from .profile_sources import profile_from_hermes_home_path


class ProfileRootUnavailable(RuntimeError):
    """Native root verification failed; the caller must refuse, not fall back."""


def native_profile_root() -> Path | None:
    """Return the native process root; only missing capability permits legacy."""
    try:
        native = importlib.import_module("hermes_constants")
    except ModuleNotFoundError as exc:
        if exc.name == "hermes_constants":
            return None
        raise ProfileRootUnavailable("hermes root module is uninspectable") from exc
    except Exception as exc:
        raise ProfileRootUnavailable("hermes root module is uninspectable") from exc
    missing = object()
    try:
        getter = getattr(native, "get_default_hermes_root", missing)
        if getter is missing:
            return None
        if not callable(getter):
            raise ProfileRootUnavailable("hermes root helper is invalid")
        root = Path(getter()).expanduser()
        if not root.is_absolute():
            raise ProfileRootUnavailable("hermes root is relative")
        return root.resolve(strict=False)
    except ProfileRootUnavailable:
        raise
    except Exception as exc:
        raise ProfileRootUnavailable("hermes root is uninspectable") from exc


def resolve_task_profile_identity(
    *, task_home: str, multiplex: bool, process_home: str = ""
) -> tuple[str, str] | None:
    """Resolve a root-bound task; None exclusively preserves the legacy path."""
    root = native_profile_root()
    if root is None:
        return None
    try:
        if not task_home or not task_home.strip():
            raise ProfileRootUnavailable("task home is missing")
        task_path = Path(task_home).expanduser()
        if not task_path.is_absolute():
            raise ProfileRootUnavailable("task home is relative")
        task_resolved = task_path.resolve(strict=False)
    except ProfileRootUnavailable:
        raise
    except Exception as exc:
        raise ProfileRootUnavailable("task home is uninspectable") from exc
    profile = profile_from_hermes_home_path(str(task_resolved), hermes_root=str(root))
    if profile is None:
        return "default", "fallback_default"
    if not multiplex and not process_home.strip():
        return None
    return profile, "hermes_home"
