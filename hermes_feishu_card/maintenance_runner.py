from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from .config import load_config, merge_card_config
from .maintenance_card import FeishuJobPublisher
from .maintenance_store import (
    MaintenancePaths,
    MaintenanceRefused,
    PROXY_ENVIRONMENT_KEYS,
    TERMINAL_UPDATE_PHASES,
    UpdateLockBusy,
    UpdateLockLease,
    UpdateJob,
    acquire_update_lock,
    consume_job_credentials,
    load_job,
    maintenance_paths,
    require_update_lock_lease,
    transition_job,
)
from .maintenance_update import _cleanup_terminal_owned_job, run_job
from .process import fetch_health
from .runner import NoopFeishuClient, build_feishu_client


_RUNNER_PHASE_RECOVERY_BOUNDARIES = {
    "locking": "no_mutation",
    "draining": "no_mutation",
    "restoring_hooks": "old_hfc_or_manual",
    "updating_hermes": "updater_result_classified",
    "reinstalling_hfc": "native_hermes",
    "starting_services": "service_recovery",
    "verifying": "service_recovery",
}


def _terminalize_owned_runner_failure(
    paths: MaintenancePaths,
    job_path: Path,
    lease: UpdateLockLease,
    *,
    error_code: str,
    base_environment: Mapping[str, str],
    proxy_environment: Mapping[str, str],
) -> UpdateJob:
    current = load_job(job_path)
    require_update_lock_lease(
        paths,
        job_id=current.job_id,
        lease=lease,
    )
    if current.phase not in TERMINAL_UPDATE_PHASES:
        current = transition_job(
            current.path,
            expected_phase=current.phase,
            phase="failed",
            result={
                "error_code": error_code,
                "recovery_boundary": _RUNNER_PHASE_RECOVERY_BOUNDARIES[current.phase],
                "status": "failed",
            },
        )
    require_update_lock_lease(
        paths,
        job_id=current.job_id,
        lease=lease,
    )
    _cleanup_terminal_owned_job(
        current,
        base_environment=base_environment,
        proxy_environment=proxy_environment,
    )
    return current


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hfc-maintenance-runner")
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    try:
        job = load_job(Path(args.job), require_private=True)
        paths = maintenance_paths(job.path.parent.parent)
    except (MaintenanceRefused, OSError, ValueError):
        return 1

    try:
        try:
            with acquire_update_lock(paths, job_id=job.job_id) as lease:
                base_environment = dict(os.environ)
                proxy_environment: dict[str, str] = {}
                try:
                    environment = consume_job_credentials(
                        paths,
                        job_id=job.job_id,
                    )
                    proxy_environment = {
                        key: value
                        for key, value in environment.items()
                        if key in PROXY_ENVIRONMENT_KEYS
                    }
                    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
                        value = environment.get(key)
                        if value:
                            os.environ[key] = value
                    base_environment = dict(os.environ)
                    config = (
                        load_config(job.config_path, env_file=job.env_file)
                        if job.env_file is not None
                        else load_config(job.config_path)
                    )
                    profile_config = _bot_profile_config(
                        _profile_config(config, job.profile_id),
                        job.bot_id,
                    )
                    client = build_feishu_client(profile_config)
                    if isinstance(client, NoopFeishuClient):
                        raise MaintenanceRefused(
                            "Feishu credentials are unavailable"
                        )
                    publisher = FeishuJobPublisher(
                        client,
                        width_mode=profile_config.get("card", {}).get("width_mode", "default"),
                    )
                except (MaintenanceRefused, OSError, ValueError):
                    result = _terminalize_owned_runner_failure(
                        paths,
                        job.path,
                        lease,
                        error_code="runner_initialization_failed",
                        base_environment=base_environment,
                        proxy_environment=proxy_environment,
                    )
                else:
                    try:
                        result = run_job(
                            job.path,
                            lock_lease=lease,
                            fetch_health=lambda: fetch_health(profile_config),
                            publish=lambda current: asyncio.run(
                                publisher.publish(current)
                            ),
                            maintenance_python=Path(sys.executable),
                            base_environment=base_environment,
                            proxy_environment=proxy_environment,
                        )
                    except (MaintenanceRefused, OSError, ValueError):
                        result = _terminalize_owned_runner_failure(
                            paths,
                            job.path,
                            lease,
                            error_code="runner_state_machine_exception",
                            base_environment=base_environment,
                            proxy_environment=proxy_environment,
                        )
        except UpdateLockBusy:
            return 0
    except (MaintenanceRefused, OSError, ValueError):
        # No verified lease: never transition, consume, publish, or clean.
        return 1

    return 0 if result.phase == "succeeded" else 1


def _profile_config(
    config: dict[str, Any],
    profile_id: str,
) -> dict[str, Any]:
    profiles = config.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        return config
    profile = profiles.get(profile_id)
    if not isinstance(profile, dict):
        raise MaintenanceRefused("maintenance profile is unavailable")
    merged = dict(config)
    for section, value in profile.items():
        if section == "card":
            merged[section] = merge_card_config(merged.get(section), value)
            continue
        if isinstance(value, dict) and isinstance(merged.get(section), dict):
            merged[section] = {**merged[section], **value}
        else:
            merged[section] = value
    return merged


def _bot_profile_config(
    config: dict[str, Any],
    bot_id: str,
) -> dict[str, Any]:
    selected = str(bot_id or "default").strip()
    bots = config.get("bots")
    items = bots.get("items") if isinstance(bots, dict) else None
    bot = items.get(selected) if isinstance(items, dict) else None
    if bot is None and selected == "default":
        return config
    if not isinstance(bot, dict):
        raise MaintenanceRefused("maintenance bot is unavailable")
    app_id = str(bot.get("app_id") or "").strip()
    app_secret = str(bot.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        raise MaintenanceRefused("maintenance bot credentials are unavailable")
    merged = dict(config)
    feishu = dict(merged.get("feishu") or {})
    for key in ("app_id", "app_secret", "base_url", "timeout_seconds"):
        if key in bot:
            feishu[key] = bot[key]
    merged["feishu"] = feishu
    merged["card"] = merge_card_config(config.get("card"), bot.get("card"))
    return merged


if __name__ == "__main__":
    raise SystemExit(main())
