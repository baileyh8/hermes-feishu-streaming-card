# Legacy Migration Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a safe migration guide from legacy/dual installs to sidecar-only.

**Architecture:** Documentation-only change guarded by `tests/unit/test_docs.py`. The guide stays conservative and does not promise automatic cleanup for old patch scripts outside the current manifest model.

**Tech Stack:** Markdown docs and pytest documentation guard.

---

### Task 1: Add Documentation Guard

**Files:**
- Modify: `tests/unit/test_docs.py`

- [x] **Step 1: Write the failing test**

The test reads `docs/migration.md`, README, installer safety docs, and TODO. It asserts the docs mention `legacy/dual`, `sidecar-only`, legacy script names, `restore --hermes-dir`, `doctor --config`, `install --hermes-dir`, `fail-closed`, credential safety, and the checked TODO item.

- [x] **Step 2: Verify red**

Run:

```bash
python3 -m pytest tests/unit/test_docs.py::test_docs_describe_safe_legacy_to_sidecar_migration -q
```

Expected: failure because `docs/migration.md` does not exist.

### Task 2: Write Migration Guide

**Files:**
- Create: `docs/migration.md`
- Modify: `README.md`
- Modify: `docs/installer-safety.md`
- Modify: `TODO.md`

- [x] **Step 1: Create `docs/migration.md`**

Document stop, backup, restore, legacy backup recovery, doctor, install, start/status, rollback, and verification checklist.

- [x] **Step 2: Link the guide**

Add README documentation link and installer-safety note.

- [x] **Step 3: Mark TODO**

Mark the P2 migration documentation item as complete.

### Task 3: Verification

- [x] **Step 1: Run focused docs test**

```bash
python3 -m pytest tests/unit/test_docs.py::test_docs_describe_safe_legacy_to_sidecar_migration -q
```

- [x] **Step 2: Run all docs tests**

```bash
python3 -m pytest tests/unit/test_docs.py -q
```

- [x] **Step 3: Run full test suite**

```bash
python3 -m pytest -q
```
