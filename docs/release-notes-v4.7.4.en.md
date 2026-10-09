# V4.7.4: sidecar runtime and process lifecycle recovery

PR #381 addresses sidecars retaining a removed Hermes managed environment and Desktop heartbeats overwriting the Gateway identity.

- **Independent runtime:** optional `service.python_executable` is honored by setup, start, and service registration. Defaults remain unchanged. The selected environment must already contain the same-version ordinary HFC package. Missing, mismatched, and editable installations are rejected without dependency installation or fallback to the managed environment.
- **Process ownership:** Gateway and Desktop/observer heartbeats are tracked separately. Maintenance requires a verified Gateway owner. Restart acknowledgement requires a new Gateway heartbeat; draining requires increasing heartbeats from the same owner.
- **Exit cleanup:** normal shutdown sends a goodbye event, including the recognized Gateway `os._exit` entry point. Local process existence and birth identity allow cleanup after exit or PID reuse within the same boot and PID namespace. Remote, unknown, and permission-denied observations remain conservative.
- **Diagnostics:** bounded process records expose missing or conflicting identities. Legacy heartbeats remain diagnostic-only and cannot authorize maintenance.

## Upgrade

Upgrade through the existing installation entry point and restart sidecar, Gateway, and Desktop through their existing service owners so every heartbeat producer loads this version. This patch does not require upgrading Hermes itself.

To isolate the sidecar from managed-environment replacement, install the same HFC version in a stable independent Python environment and configure `service.python_executable`; see the [user guide](user-guide.en.md). This is opt-in and does not migrate existing installations automatically.

Current Hermes hooks and installation integrity are still verified. This release does not bypass `manual_review_required`, unsupported Hermes builds, or source ownership checks. Do not edit Hermes core files manually.

## Validation and limits

Real local acceptance separately restarted Gateway and Desktop, preserving the other process identity and removing exited records without restarting the sidecar. Tests cover real child-process exit, PID reuse, remote/unknown identities, legacy protocol, signed HTTP, and ordinary installed-package imports. Final CI, exact merge commit, and public artifact verification are recorded in the Release.

No card layout changed and no new real Feishu conversation or mobile visual acceptance is claimed. Unknown Hermes exit entry points may not send an explicit goodbye; verified local process cleanup handles exits, while uncertain identity continues to block maintenance.
