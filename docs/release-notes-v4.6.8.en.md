# V4.6.8: macOS installation and recovery guidance

When a persistent service is unavailable, macOS `setup` explains the platform limitation instead of suggesting the Linux-systemd-only `enable` command. Users who need login-time startup may configure a one-shot LaunchAgent with `RunAtLoad` to invoke `hermes-feishu-card start`, without `KeepAlive`, using the actual absolute executable, config, env-file and Hermes paths. HFC does not install, remove or manage launchd services.

An existing process without a verified pidfile is still refused; that condition does not establish launchd ownership. Manual stop instructions depend on confirming the actual owner and LaunchAgent label. Other processes must be stopped through their actual manager. A one-shot `start` creates a detached child, so stopping the LaunchAgent alone does not establish that the sidecar stopped. Both installer-safety languages distinguish these paths.

This release changes CLI platform guidance, documentation and version markers. Linux guidance, PID/token/health ownership checks and card behavior remain unchanged. Thanks to [coder-zhw](https://github.com/coder-zhw) for the macOS investigation, implementation and regression tests in [PR #347](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/347).

## Validation scope

New validation covers the macOS/Linux CLI branches, recovery guidance for an unknown process owner, and the same command paths in an ordinary package installation. Full tests, platform CI, the exact merge commit, release asset checksums and a public-tag ordinary installation remain release gates; final results belong in the GitHub Release delivery record.

Guidance tests do not establish real LaunchAgent installation, login-time startup or shutdown. This release adds no mobile visual or Feishu card-interaction acceptance claim. Production deployment and package verification are recorded separately.
