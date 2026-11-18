"""Fixed source-family identities; layout alone does not prove hook support."""

from pathlib import Path

DECOMPOSED_GATEWAY_TARGETS = (
    "gateway/run_background.py",
    "gateway/run_turn.py", "gateway/run_turn_persistence.py", "gateway/run_turn_prepare.py", "gateway/run_turn_hygiene.py", "gateway/run_turn_runner.py", "gateway/run_inbound.py",
    "gateway/run_busy.py", "gateway/run_startup.py", "gateway/run_notifications.py",
)
DECOMPOSED_TARGETS = (*DECOMPOSED_GATEWAY_TARGETS,
                      "cron/scheduler_delivery.py", "gateway/platforms/base.py")


DECOMPOSED_MANIFEST_VERSION = 4
DECOMPOSED_LAYOUT = "gateway-decomposed-v1"
MANIFEST_NAME = ".hermes_feishu_card_manifest"
BACKUP_SUFFIX = ".hermes_feishu_card.bak"
DECOMPOSED_SOURCE_TARGETS = (
    "gateway/run.py", *DECOMPOSED_GATEWAY_TARGETS,
    "cron/scheduler.py", "cron/scheduler_delivery.py", "gateway/platforms/base.py",
)

# Read-only contract peers. These are never managed, backed up, or patched.
DECOMPOSED_PEER_SOURCES = (
    "gateway/run_turn_setup_guard.py",
    "gateway/run_turn_runner_approval_transport.py",
    "gateway/platforms/base_delivery_ledger.py",
    "gateway/platforms/base_session_command.py",
)


def read_peer_contract_sources(root: Path) -> dict[str, str]:
    """Load optional extracted peers used to verify bindings, not hook targets."""
    root = Path(root)
    sources: dict[str, str] = {}
    for name in DECOMPOSED_PEER_SOURCES:
        path = root / name
        if path.is_symlink():
            raise ValueError(f"{name} must not be a symlink")
        if path.exists():
            sources[name] = path.read_text(encoding="utf-8")
    return sources
