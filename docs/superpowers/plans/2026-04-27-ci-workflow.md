# CI Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal GitHub Actions pytest workflow.

**Architecture:** A repository-level workflow installs the package with test extras and runs the existing pytest suite across Python 3.9 and 3.12. A small unit test guards the workflow content.

**Tech Stack:** GitHub Actions, Python, pytest.

---

### Task 1: Add CI Guard Test

**Files:**
- Create: `tests/unit/test_ci_workflow.py`

- [x] **Step 1: Write failing test**

Assert `.github/workflows/tests.yml` contains `pull_request`, `push`, Python matrix `["3.9", "3.12"]`, install command, and pytest command.

- [x] **Step 2: Verify red**

```bash
python3 -m pytest tests/unit/test_ci_workflow.py -q
```

Expected: fails because workflow is missing.

### Task 2: Add Workflow

**Files:**
- Create: `.github/workflows/tests.yml`

- [x] **Step 1: Implement workflow**

Use `actions/checkout@v4`, `actions/setup-python@v5`, `python -m pip install -e ".[test]"`, and `python -m pytest -q`.

- [x] **Step 2: Verify guard test**

```bash
python3 -m pytest tests/unit/test_ci_workflow.py -q
```

### Task 3: Verification

- [x] **Step 1: Run full suite**

```bash
python3 -m pytest -q
```
