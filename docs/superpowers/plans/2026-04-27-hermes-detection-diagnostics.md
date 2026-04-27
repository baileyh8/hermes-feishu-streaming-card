# Hermes Detection Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only Hermes detection diagnostics to doctor/install output.

**Architecture:** Extend the existing `HermesDetection` dataclass with metadata and centralize CLI formatting in `cli.py`. Keep all detection fail-closed and do not modify installer patching behavior.

**Tech Stack:** Python 3.9+, argparse, pathlib, existing pytest integration tests.

---

## Task 1: Detection Metadata

**Files:**
- Modify: `hermes_feishu_card/install/detect.py`
- Modify: `tests/unit/test_installer_detection.py`

- [ ] Add `version_source`, `minimum_version`, and `run_py_exists` to `HermesDetection`.
- [ ] Update tests to assert `VERSION`, `git tag`, and `unknown` sources.
- [ ] Run `python3 -m pytest tests/unit/test_installer_detection.py -q`.

## Task 2: CLI Diagnostics

**Files:**
- Modify: `hermes_feishu_card/cli.py`
- Modify: `tests/integration/test_cli.py`
- Modify: `tests/integration/test_cli_install.py`

- [ ] Add `doctor --hermes-dir`.
- [ ] Keep `doctor --skip-hermes`.
- [ ] Add a shared `_format_detection()` helper for install failure and doctor output.
- [ ] Update install unsupported tests to assert diagnostic fields.
- [ ] Run `python3 -m pytest tests/integration/test_cli.py tests/integration/test_cli_install.py -q`.

## Task 3: Docs And Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/installer-safety.md`
- Modify: `docs/testing.md`
- Modify: `TODO.md`
- Modify: `tests/unit/test_docs.py`

- [ ] Document `doctor --hermes-dir`.
- [ ] Mark friendlier install diagnostics as done.
- [ ] Add doc guard for version source/minimum version diagnostics.
- [ ] Run `python3 -m pytest -q`.
- [ ] Commit with `feat: improve Hermes detection diagnostics`.
