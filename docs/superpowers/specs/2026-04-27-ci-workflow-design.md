# CI Workflow Design

## Goal

Add a minimal GitHub Actions workflow so pull requests and pushed branches run the same pytest suite used locally.

## Scope

This change only adds CI for tests. It does not publish packages, upload artifacts, run real Feishu smoke tests, or require secrets.

## Design

Create `.github/workflows/tests.yml` with a Python matrix for 3.9 and 3.12. Each job checks out the repository, installs `.[test]`, and runs `python -m pytest -q`.

The workflow triggers on pull requests and pushes to `main` and `codex/**` branches.

## Verification

Add `tests/unit/test_ci_workflow.py` to guard the workflow trigger, Python matrix, install command, and pytest command.
