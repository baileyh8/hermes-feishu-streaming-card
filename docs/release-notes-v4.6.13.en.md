# V4.6.13: keep control credentials out of process arguments

Fix #367: managed sidecars use `--token-file`. Runner and transient systemd arguments contain only a path. The token is atomically stored in private state as `sidecar-control.token` with POSIX mode 0600. Reads reject links, non-regular files, wrong ownership, permissive modes, oversized or invalid contents.

- Unsafe credential-file writes/reads fail explicitly without plaintext-argv fallback. PID/token/health ownership and authenticated shutdown remain intact.
- Keep the file for automatic systemd restarts and rotate it on the next managed launch. Detached, explicit systemd user/system and persistent startup paths use the file. Manual legacy `--token` remains supported but cannot be combined with `--token-file`.
- Restart the sidecar through its existing service manager after upgrading; a running process keeps its original argv. Same-user processes remain inside the local trust boundary. This reduces process-list/screenshot exposure and does not prevent same-user file access. Windows ACL privacy remains subject to existing platform limits; POSIX modes are not ACL verification.
- Includes the spinner-test stabilization from PR #366. Stable Hermes remains v2026.9.24 / 0.21.5; the current main source snapshot is `7817bf522af3caf54b30ae59f16157469d7638fc`.

Thanks to [ffdxdynotable](https://github.com/ffdxdynotable) for [#367](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/367), reporting control-token exposure with Termux evidence and a private-file proposal. All historical credits are retained; issue evidence/proposals are distinguished from repository code authorship.

Final CI, ordinary installation, real subprocess startup/authenticated shutdown and asset checks are recorded in the GitHub Release. Production, Termux devices and real Feishu Gateway tasks were not upgraded or accepted.
