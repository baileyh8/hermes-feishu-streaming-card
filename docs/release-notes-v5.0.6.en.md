# V5.0.6: installer archive directories and metadata

This release addresses [#454](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/454). The v5.0.5 Linux/macOS tarballs contain a `./` entry with uid/gid 1001:1001 and mode 0755. Extractors can apply that metadata to the extraction target itself. Local non-root macOS bsdtar reproduced 0700/1777 targets changing to 0755 with `-p`. The reporter's Linux root ownership change and host-service failure have not been independently reproduced.

## Scope

Each platform asset has one matching versioned top-level directory:

- `hermes-feishu-card-v5.0.6-linux/`
- `hermes-feishu-card-v5.0.6-macos/`
- `hermes-feishu-card-v5.0.6-windows/`

Each directory contains the original nine files. Tar uid/gid are normalized to 0; directories and shell installers use 0755, and other regular files use 0644. ZIP follows the same directory and file-mode convention. There is no `.` member referring directly to the extraction target. Owner normalization alone retains the old mode problem, so the versioned top-level directory is also required.

Construction and validation share the archive contract, checking the exact inventory, bytes and metadata while rejecting absolute/parent/backslash paths, links, special files and duplicates. Actual extraction regressions use isolated 0700/1777 scratch targets and check destination inode/uid/gid/mode, existing sentinels and extracted files, with an old-layout negative control. Linux root checks run only inside a verified CI-owned scratch tree.

## Installation path change

Old flat assets placed `./install.sh` directly in the extraction target. Enter the new platform subdirectory before running the installer. This Linux example assumes the asset has been downloaded and its checksum verified; use it after the version is published:

```bash
mkdir hfc-install-v5.0.6
# Extract inside a new dedicated directory, away from shared temporary paths.
tar -xzf hermes-feishu-card-v5.0.6-linux.tar.gz -C hfc-install-v5.0.6
cd hfc-install-v5.0.6/hermes-feishu-card-v5.0.6-linux
./install.sh --version v5.0.6
```

For macOS, enter `hermes-feishu-card-v5.0.6-macos/`. On Windows, extract the ZIP, enter `hermes-feishu-card-v5.0.6-windows/`, then run `.\install.ps1`. See [installation instructions](../README-install.md). Existing v5.0.5 assets and tags retain their published history.

## Verification boundary

Local old-archive mode rewriting and versioned-directory prototype comparisons have evidence. Final focused checks, Linux GNU tar root/non-root, macOS actual extraction, exact-commit full CI/CodeQL, ordinary wheels, public assets and anonymous downloads are recorded separately in [release readiness](release-readiness.en.md). Prototype or historical v5.0.5 results cannot establish a final candidate pass. During release preparation, Linux/root still requires CI evidence; the reporter's host recovery is not claimed.

This packaging and release change adds no Gateway/model or real Feishu client acceptance. Existing unverified Gateway/model, desktop and mobile boundaries remain recorded.

## Credits

Thanks to [leavrcn](https://github.com/leavrcn) for the #454 report and reproduction evidence. This is issue-evidence credit; Git commits determine code authorship, and the report does not justify a `Co-authored-by` trailer. The release interval starts after v5.0.5. All earlier contributors, commits and historical release notes remain intact.
