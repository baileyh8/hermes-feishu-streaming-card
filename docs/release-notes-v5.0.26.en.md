# v5.0.26: Hermes v0.23.2 compatibility

HFC now recognizes the official Hermes v0.23.2 source at `ae2a81ccc00e9b7c06301523e5bb09608397149c`. The previous release returned `unsupported` in `doctor`, blocking the normal setup and upgrade path. [#532](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/532)

- Recognize the extracted turn source binding, approval notification transport, final delivery ledger, and stop cancellation path. Approval ownership and generation checks still belong to Hermes.
- Support the official PM writer for config schema 53. Recovery binds the contents and presence of read-only contract files. Managed files can be restored byte for byte.
- Add the pinned v0.23.2 official image to CI. Ubuntu 24.04 runs the Linux Python 3.9 regression and extraction checks. Update the Ruff development pin to 0.17.5. [#531](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/531)

Follow the [installation guide](../README-install.md) and retain existing manifests and backups. `doctor` reports source compatibility and installation state. The [GitHub Release](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.26) records exact-commit CI, packaged installation, live Gateway/model, and Feishu client acceptance separately.

Thanks to [Lite-G](https://github.com/Lite-G) for the reproducible report and compatibility implementation. [#532](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/532) · [#533](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/533)
