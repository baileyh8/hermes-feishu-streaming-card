# Release Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve package metadata and release readiness documentation.

**Architecture:** Metadata-only and documentation-only updates guarded by unit tests. Runtime code remains unchanged.

**Tech Stack:** Python packaging metadata, Markdown docs, pytest.

---

### Task 1: Package Metadata

**Files:**
- Modify: `tests/unit/test_package_metadata.py`
- Modify: `pyproject.toml`

- [x] **Step 1: Write failing metadata test**

Assert README, keywords, classifiers, repository URL, and issues URL exist in `pyproject.toml`.

- [x] **Step 2: Verify red**

```bash
python3 -m pytest tests/unit/test_package_metadata.py::test_pyproject_has_open_source_package_metadata -q
```

- [x] **Step 3: Add metadata**

Add `readme`, `keywords`, `classifiers`, and `[project.urls]`.

### Task 2: Release Readiness Docs

**Files:**
- Create: `docs/release-readiness.md`
- Modify: `README.md`
- Modify: `tests/unit/test_docs.py`

- [x] **Step 1: Write failing docs test**

Assert README links `docs/release-readiness.md` and the doc mentions version `0.1.0`, local pytest, real Hermes Gateway, real Feishu app, App Secret safety, and GitHub Actions.

- [x] **Step 2: Create docs**

Document what is ready, what must be manually verified, and current boundaries.

### Task 3: Verification

- [x] **Step 1: Run full suite**

```bash
python3 -m pytest -q
```
