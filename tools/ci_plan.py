"""Select conservative PR regression scope; releases and main always run full."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOW_RISK_GROUPS = {"docs", "render", "appearance", "quota", "config"}
DOC_FILES = {"README.md", "README.en.md", "CHANGELOG.md", "AGENTS.md", "TODO.md", "LICENSE"}


def select(paths, root=ROOT):
    routing = json.loads((root / "tools/preflight_routes.json").read_text())
    targets = set(routing["groups"]["docs"])
    groups = {"docs"}
    if not paths:
        return "full", []
    for path in paths:
        if path.startswith("docs/") or path in DOC_FILES:
            continue
        source_groups = set(routing["sources"].get(path, []))
        if not source_groups or not source_groups <= LOW_RISK_GROUPS or not (root / path).is_file():
            return "full", []
        groups.update(source_groups)
    for group in groups:
        targets.update(routing["groups"][group])
    # Deleted test files, routing drift and an unknown source fail back to full.
    if any(not (root / target).is_file() for target in targets):
        return "full", []
    return ("docs" if groups == {"docs"} else "focused"), sorted(targets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    mode, targets = "full", []
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request" and not args.full:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        base = event["pull_request"]["base"]["sha"]
        diff = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, "HEAD", "--"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        mode, targets = select(list(filter(None, diff.stdout.split("\0"))))
    plan = {"schema": 1, "mode": mode, "targets": targets}
    args.output.write_text(json.dumps(plan, indent=2) + "\n")
    versions = ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"] if mode == "full" else ["3.12"]
    slices = [0, 1] if mode != "docs" else [0]
    matrix = {"python-version": versions, "slice": slices}
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"mode={mode}\npytest_matrix={json.dumps(matrix)}\n")
    print(json.dumps({"mode": mode, "matrix": matrix, "target_count": len(targets)}))


if __name__ == "__main__":
    main()
