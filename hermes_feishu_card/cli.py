from __future__ import annotations

import argparse
import sys

from hermes_feishu_card.config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "status":
        return _run_status()

    parser.print_help()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-feishu-card")
    subparsers = parser.add_subparsers(dest="command")

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--config", required=True)
    doctor.add_argument("--skip-hermes", action="store_true")

    subparsers.add_parser("status")
    return parser


def _run_doctor(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    host = config["server"]["host"]
    port = config["server"]["port"]
    print("doctor: ok")
    print(f"sidecar: {host}:{port}")
    if args.skip_hermes:
        print("hermes: skipped")
    return 0


def _run_status() -> int:
    print("status: process management not implemented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
