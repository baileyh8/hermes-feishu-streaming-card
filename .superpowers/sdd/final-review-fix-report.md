# v3.9.0 Final Review Fix Follow-up

## Completed

- Kept the full recovery fingerprint inside `OperationRecord` and normal operation callbacks; card and HTTP output retain the truncated display value.
- Validated existing and raced transport-root secrets with `lstat`, regular-file checks, symlink rejection, length checks, and POSIX permission checks.
- Preserved a canonical session key when it has been reassigned as an alias to a newer session.
- Passed an explicit setup env file through the sidecar start command to the runner, where it takes precedence for Hermes root resolution.
- Applied one recursive JSON redactor to doctor output, including config-load errors and absolute paths embedded in text.

## Verification

The targeted operations, server, transport, lifecycle, config, runner, CLI, install, and diagnostics tests passed: 395 passed.

## Follow-up

Run the normal real Feishu/Lark smoke checklist before publishing the v3.9.0 release artifact; no external delivery was performed in this change.
