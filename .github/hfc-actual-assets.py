"""Verify downloaded assets using the release's existing extraction contract."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--assets", type=Path, required=True)
parser.add_argument("--tag", required=True)
parser.add_argument("--evidence", type=Path, required=True)
parser.add_argument("--root", action="store_true")
args = parser.parse_args()
source = args.source.resolve(strict=True)
assets_dir = args.assets.resolve(strict=True)
evidence = args.evidence.resolve()
evidence.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(source))
from scripts.release_archives import validate_assets  # noqa: E402

validate_assets(source, assets_dir, args.tag)
spec = importlib.util.spec_from_file_location(
    "hfc_actual_asset_contract", source / "tests/unit/test_release_archives.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.ROOT = source
module.TAG = args.tag
assets = sorted(p for p in assets_dir.iterdir() if p.name.endswith((".tar.gz", ".zip")))
scratch = Path(tempfile.mkdtemp(prefix="hfc-release-extract-", dir=evidence))
scratch_identity = (scratch.stat().st_dev, scratch.stat().st_ino, scratch.stat().st_uid)
os.environ["HFC_ARCHIVE_EVIDENCE_DIR"] = str(evidence)
if args.root:
    assert sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
    os.environ["HFC_ARCHIVE_ROOT_TEST"] = "1"
previous_mask = os.umask(0o022)
try:
    for root in ([False, True] if args.root else [False]):
        for preserve in [False, True]:
            for mode in [0o700, 0o1777]:
                case = scratch / f"root{int(root)}-p{int(preserve)}-{mode:o}"
                case.mkdir()
                module.test_real_tar_preserves_destination_and_sentinels(
                    assets, case, mode, preserve, root
                )
finally:
    os.umask(previous_mask)
    assert scratch_identity == (
        scratch.stat().st_dev, scratch.stat().st_ino, scratch.stat().st_uid
    )
    assert scratch.parent == evidence and scratch.stat().st_uid == os.getuid()
    shutil.rmtree(scratch)
result = {
    "tag": args.tag,
    "platform": sys.platform,
    "root_and_nonroot": args.root,
    "cases": 8 if args.root else 4,
    "all_payloads_and_metadata_match_source": True,
    "all_checksums_match": True,
    "negative_flat_archive_detected": True,
    "assets": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(assets_dir.iterdir())},
}
(evidence / "actual-assets-summary.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, sort_keys=True))
