"""Isolated #526 native-entry control; model and sidecar are loopback stubs.

Run with an existing SDK interpreter. This does not exercise the PM launcher,
Feishu cloud delivery, a real model, or an already-running Gateway process.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PIN = "3b421578588243f920886e6fe79a6faa87ea026e"


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-root", type=Path, required=True)
    parser.add_argument("--managed-root", type=Path)
    parser.add_argument("--hfc-source", type=Path, help="Development mode only; omit for an installed wheel")
    parser.add_argument("--expected-package-root", type=Path, help="Required in installed-wheel mode")
    parser.add_argument("--output", type=Path, required=True, help="New, empty task-owned directory")
    args = parser.parse_args()
    upstream = args.upstream_root.resolve()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("output must be new or empty; existing evidence is never overwritten")
    if not args.hfc_source and not args.expected_package_root:
        parser.error("installed-wheel mode requires --expected-package-root")
    if git(upstream, "rev-parse", "HEAD").decode().strip() != PIN or git(upstream, "status", "--porcelain"):
        parser.error("upstream must be a clean Git checkout of pinned v0.23.1")
    output.mkdir(parents=True, exist_ok=True)
    home = output / "child" / "home"
    temp = output / "child" / "tmp"
    home.mkdir(parents=True)
    temp.mkdir()
    # Retain Windows DLL / Git lookup prerequisites, discard all provider keys,
    # proxies, Python path overrides and formal Hermes/Feishu configuration.
    env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}}
    env.update(HOME=str(home), USERPROFILE=str(home), HERMES_HOME=str(home),
               TMPDIR=str(temp), TMP=str(temp), TEMP=str(temp), NO_PROXY="*",
               APPDATA=str(home / "AppData" / "Roaming"),
               LOCALAPPDATA=str(home / "AppData" / "Local"))
    os.environ.clear()
    os.environ.update(env)
    sys.dont_write_bytecode = True
    source = args.hfc_source.resolve() if args.hfc_source else output / "empty-source"
    if args.hfc_source:
        sys.path.insert(0, str(source))
    import hermes_feishu_card as hfc
    origin = Path(hfc.__file__).resolve()
    expected = source if args.hfc_source else args.expected_package_root.resolve()
    if not origin.is_relative_to(expected):
        raise RuntimeError("HFC import origin is outside the declared package/source root")
    from hermes_feishu_card.install.decomposed_ownership import render
    from hermes_feishu_card.install.source_layout import BACKUP_SUFFIX, DECOMPOSED_SOURCE_TARGETS, MANIFEST_NAME
    originals = {name: git(upstream, "show", f"HEAD:{name}") for name in DECOMPOSED_SOURCE_TARGETS}
    rendered = render(originals, strategy="gateway_run_013_plus")
    managed = args.managed_root.resolve() if args.managed_root else output / "managed-copy"
    if args.managed_root:
        for name in DECOMPOSED_SOURCE_TARGETS:
            if (managed / name).read_bytes() != rendered[name]:
                raise RuntimeError(f"managed target differs from pinned renderer output: {name}")
    else:
        shutil.copytree(upstream, managed, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", "venv", "node_modules"))
        manifest = {"manifest_version": 4, "layout": "gateway-decomposed-v1",
                    "integration_mode": "legacy-patch", "targets": {}}
        for name in DECOMPOSED_SOURCE_TARGETS:
            (managed / name).write_bytes(rendered[name])
            (managed / (name + BACKUP_SUFFIX)).write_bytes(originals[name])
            manifest["targets"][name] = {"path": name, "backup": name + BACKUP_SUFFIX,
                "original_sha256": hashlib.sha256(originals[name]).hexdigest(),
                "patched_sha256": hashlib.sha256(rendered[name]).hexdigest()}
        (managed / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        # Sparse fixtures may have omitted non-Python plugin metadata.
        name = "plugins/platforms/feishu/plugin.yaml"
        (managed / name).write_bytes(git(upstream, "show", f"HEAD:{name}"))
    os.environ.update(HFC526_SOURCE_OR_EMPTY=str(source), HFC526_EMPTY_TESTLIBS=str(output / "empty-testlibs"),
                      HFC526_MANAGED_ROOT=str(managed), HFC526_OUTPUT_ROOT=str(output))
    spec = importlib.util.spec_from_file_location("hfc526_portable_driver", Path(__file__).with_name("driver.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    servers = []
    start_stub = module.start_stub

    def tracked_stub(handler):
        server = start_stub(handler)
        servers.append(server)
        return server

    module.start_stub = tracked_stub
    try:
        code = module.main()
        receipt = module.RECEIPT
        starts = [e["message_id"] for e in receipt["events"] if e.get("event") == "message.started"]
        checks = {"two_turns": len(starts) == 2 and len(set(starts)) == 2,
                  "model_requests": len(receipt["model_requests"]) >= 2,
                  "no_recorded_errors": not receipt["errors"],
                  "native_loader": receipt["files"].get("feishu_adapter_loader_module") == "hermes_plugins.platforms__feishu.adapter"}
        for mid in starts:
            events = [e for e in receipt["events"] if (e.get("turn_id") or e.get("message_id")) == mid]
            names = [e["event"] for e in events]
            rows = [r for r in receipt["admissions"] if isinstance(r, dict) and r.get("request_id") == mid]
            hooks = [h for h in receipt["hook_calls"] if h["event_name"] == "message.started" and h.get("identity", {}).get("event_message_id") == mid]
            # system.notice is a supported turn event and consumes a sequence.
            # Require one complete, contiguous stream and the exact fake answer;
            # a valid native status notice must not become a false test failure.
            checks[mid] = (bool(events) and names[0] == "message.started" and names[-1] == "message.completed"
                and names.count("message.started") == names.count("message.completed") == 1
                and "answer.delta" in names
                and set(names) <= {"message.started", "answer.delta", "system.notice", "message.completed"}
                and [e["sequence"] for e in events] == list(range(len(events)))
                and events[-1].get("answer") == "fixture reply"
                and "".join(e.get("delta_text") or "" for e in events if e["event"] == "answer.delta") == "fixture reply"
                and len(rows) == 1 and rows[0]["status"] == "terminal" and rows[0]["outcome"] == "completed"
                and len(hooks) == 1 and hooks[0]["admission_mode"] == "handoff" and hooks[0]["result"] is True
                and hooks[0]["identity"].get("authority_runner_is_runner") is True
                and hooks[0]["identity"].get("live_task_running") is True
                and hooks[0]["identity"].get("owner_session_id") == rows[0]["target_session_id"]
                and hooks[0]["identity"].get("owner_generation") == rows[0]["generation"]
                and hooks[0]["identity"].get("owner_generation") == hooks[0]["identity"].get("executing_generation"))
        result = {"passed": code == 0 and all(checks.values()), "checks": checks,
                  "hfc_version": hfc.__version__, "hfc_origin": str(origin), "python": sys.version,
                  "platform": sys.platform, "upstream_pin": PIN,
                  "scope": "real native inbound/admission; synthetic Feishu input; loopback model/sidecar stubs; no cloud/client/PM-launcher proof",
                  "package_mode": "development source" if args.hfc_source else "installed package (provenance separately required)"}
        (output / "ACCEPTANCE.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result))
        return 0 if result["passed"] else 1
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        try:
            from gateway.runtime_ownership import process_ownership
            process_ownership.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
