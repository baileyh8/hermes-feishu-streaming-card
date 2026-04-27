# Release Readiness Design

## Goal

Improve open-source package readiness without changing runtime behavior.

## Scope

This change updates package metadata and adds release readiness documentation. It does not choose a license, publish packages, run real Feishu smoke tests, or modify sidecar behavior.

## Design

Add README-based package metadata, project URLs, keywords, and Python classifiers to `pyproject.toml`. Add `docs/release-readiness.md` to state what is already automated, what must still be manually verified, and how to handle credentials during real smoke tests.

## Verification

Tests guard the package metadata and release readiness documentation. Full pytest remains the final local verification.
