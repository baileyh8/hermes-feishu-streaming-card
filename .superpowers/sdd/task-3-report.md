# Task 3 Report — Compose Example and Release Packaging (GREEN)

## Scope
- Files modified:
  - `docker-compose.example.yml`
  - `.github/workflows/release-assets.yml`
  - `tests/unit/test_ci_workflow.py`
  - `.superpowers/sdd/task-3-report.md`

## Changes made
- Added docker compose example file for installer consumption:
  - `docker-compose.example.yml` using placeholder image `your-hermes-image:latest`
  - default container paths:
    - `HERMES_DIR=/opt/hermes`
    - `HFC_CONFIG=/opt/data/config.yaml`
    - `HFC_ENV_FILE=/opt/data/.env`
  - wired credentials/env placeholders and mounts `install-docker.sh` to `/tmp/install-docker.sh`
- Updated `.github/workflows/release-assets.yml` package copy list to include:
  - `install-docker.sh`
  - `docker-compose.example.yml`
  for macOS/Linux/Windows packages.
- Extended `tests/unit/test_ci_workflow.py`:
  - `test_release_assets_workflow_supports_manual_package_dry_run()` now asserts workflow includes `install-docker.sh` and `docker-compose.example.yml`.
  - added `test_docker_compose_example_documents_container_paths()` to validate compose content and paths/env placeholders.

## Test commands
```bash
.venv/bin/python -m pytest tests/unit/test_ci_workflow.py::test_release_assets_workflow_supports_manual_package_dry_run tests/unit/test_ci_workflow.py::test_docker_compose_example_documents_container_paths -q
```
（预期失败）

```bash
.venv/bin/python -m pytest tests/unit/test_ci_workflow.py -q
```
结果：`3 passed`

## Follow-up
- Version remains `v3.7.0` for release context and no test path requires Docker daemon access.
