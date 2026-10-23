"""Release asset bytes and real tar extraction, independent of Hermes fixtures."""

from __future__ import annotations

import gzip
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts.release_archives import ArchiveError, build_assets, validate_asset

ROOT = Path(__file__).resolve().parents[2]
TAG = "v5.0.6"


def snapshot(path):
    value = path.lstat()
    return value.st_ino, value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode)


@pytest.fixture
def assets(tmp_path):
    return build_assets(ROOT, tmp_path / "dist", TAG)


def test_built_assets_have_exact_contents_and_metadata(assets):
    assert len(assets) == 3
    for asset in assets:
        platform = asset.name.split("-")[-1].split(".")[0]
        validate_asset(asset, ROOT, TAG, platform)
        if asset.suffix == ".zip":
            with zipfile.ZipFile(asset) as archive:
                assert len(archive.infolist()) == 10
        else:
            with tarfile.open(asset) as archive:
                assert len(archive.getmembers()) == 10
                assert all(m.name not in (".", "./") for m in archive)


@pytest.mark.parametrize(
    "mutation",
    [
        "root",
        "absolute",
        "parent",
        "dot",
        "double_slash",
        "backslash",
        "duplicate",
        "symlink",
        "hardlink",
        "fifo",
        "owner",
        "mode",
        "name",
        "bytes",
        "missing",
        "extra",
    ],
)
def test_validator_rejects_unsafe_or_drifted_tar(assets, tmp_path, mutation):
    source = next(a for a in assets if a.name.endswith("-linux.tar.gz"))
    with tarfile.open(source) as archive:
        rows = [
            (m, archive.extractfile(m).read() if m.isfile() else None) for m in archive
        ]
    member, data = rows[1]
    if mutation == "root":
        rows[0][0].name = "."
    elif mutation == "absolute":
        member.name = "/" + member.name
    elif mutation == "parent":
        member.name = "../" + member.name
    elif mutation == "dot":
        member.name = member.name.replace("/", "/./")
    elif mutation == "double_slash":
        member.name = member.name.replace("/", "//")
    elif mutation == "backslash":
        member.name = member.name.replace("/", "\\")
    elif mutation == "duplicate":
        rows.append((member, data))
    elif mutation in ("symlink", "hardlink", "fifo"):
        member.type = {
            "symlink": tarfile.SYMTYPE,
            "hardlink": tarfile.LNKTYPE,
            "fifo": tarfile.FIFOTYPE,
        }[mutation]
        member.linkname = "../../sentinel"
        member.size = 0
    elif mutation == "owner":
        member.uid = 1001
    elif mutation == "mode":
        member.mode = 0o4755
    elif mutation == "name":
        member.uname = "runner"
    elif mutation == "bytes":
        rows[1] = (member, b"x" * member.size)
    elif mutation == "missing":
        rows.pop()
    elif mutation == "extra":
        extra = tarfile.TarInfo(rows[0][0].name + "/extra")
        rows.append((extra, b""))
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as archive:
        for info, content in rows:
            archive.addfile(info, io.BytesIO(content) if content is not None else None)
    with pytest.raises(ArchiveError):
        validate_asset(bad, ROOT, TAG, "linux")


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "symlink",
        "path",
        "mode",
        "bytes",
        "absolute",
        "backslash",
        "dot",
        "extra",
        "owner_system",
        "directory_mode",
        "missing",
    ],
)
def test_validator_rejects_drifted_zip(assets, tmp_path, mutation):
    source = next(a for a in assets if a.suffix == ".zip")
    with zipfile.ZipFile(source) as archive:
        rows = [(m, archive.read(m)) for m in archive.infolist()]
    member, content = rows[1]
    if mutation == "duplicate":
        rows.append((member, content))
    elif mutation == "symlink":
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
    elif mutation == "path":
        member.filename = "../escape"
    elif mutation == "mode":
        member.external_attr = (stat.S_IFREG | 0o777) << 16
    elif mutation == "bytes":
        rows[1] = (member, b"wrong")
    elif mutation == "absolute":
        member.filename = "/" + member.filename
    elif mutation == "backslash":
        member.filename = member.filename.replace("/", "\\")
    elif mutation == "dot":
        member.filename = member.filename.replace("/", "/./")
    elif mutation == "extra":
        member.extra = b"ux\x00\x00"
    elif mutation == "owner_system":
        member.create_system = 0
    elif mutation == "directory_mode":
        rows[0][0].external_attr = (stat.S_IFDIR | 0o1777) << 16
    elif mutation == "missing":
        rows.pop()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        for info, data in rows:
            archive.writestr(info, data)
    with pytest.raises(ArchiveError):
        validate_asset(bad, ROOT, TAG, "windows")


@pytest.mark.skipif(
    sys.platform not in ("darwin", "linux"), reason="POSIX tar contract"
)
@pytest.mark.parametrize("mode", [0o700, 0o1777])
@pytest.mark.parametrize("preserve", [False, True])
@pytest.mark.parametrize("as_root", [False, True])
def test_real_tar_preserves_destination_and_sentinels(
    assets, tmp_path, mode, preserve, as_root
):
    if as_root and os.environ.get("HFC_ARCHIVE_ROOT_TEST") != "1":
        pytest.skip("root extraction is an explicit isolated Linux CI gate")
    if as_root:
        assert sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
        assert subprocess.check_output(["tar", "--version"], text=True).startswith(
            "tar (GNU tar)"
        )
    sandbox = tmp_path / "owned-extraction"
    sandbox.mkdir(mode=0o700)
    identity = snapshot(sandbox)
    assert identity[1] == os.getuid() and identity[3] == 0o700
    destination = sandbox / "destination"
    destination.mkdir()
    destination.chmod(mode)
    sentinel = sandbox / "sentinel"
    sentinel.write_bytes(b"keep neighbor")
    inner = destination / "sentinel"
    inner.write_bytes(b"keep existing")
    before = {
        p: (snapshot(p), p.read_bytes() if p.is_file() else None)
        for p in (destination, sentinel, inner)
    }
    platform = "macos" if sys.platform == "darwin" else "linux"
    asset = next(a for a in assets if a.name.endswith("-" + platform + ".tar.gz"))
    command = (["sudo", "-n"] if as_root else []) + [
        "tar",
        "-xpzf" if preserve else "-xzf",
        str(asset),
        "-C",
        str(destination),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
        for path, expected in before.items():
            assert (
                snapshot(path),
                path.read_bytes() if path.is_file() else None,
            ) == expected
        top = destination / ("hermes-feishu-card-" + TAG + "-" + platform)
        assert len(list(top.iterdir())) == 9
        for path in top.iterdir():
            assert path.stat().st_uid == (0 if as_root else os.getuid())
            assert path.stat().st_gid == (0 if as_root else os.getgid())
            assert path.read_bytes() == (ROOT / path.name).read_bytes()
            assert stat.S_IMODE(path.stat().st_mode) == (
                0o755 if path.name.endswith(".sh") else 0o644
            )
        # The old root entry must change metadata: prove this regression can fail.
        old = sandbox / "old.tar.gz"
        with tarfile.open(old, "w:gz") as archive:
            member = tarfile.TarInfo(".")
            member.type = tarfile.DIRTYPE
            member.mode = 0o755
            member.uid = member.gid = 4242
            archive.addfile(member)
        negative = destination / "negative"
        negative.mkdir()
        negative.chmod(mode)
        prior = snapshot(negative)
        negative_preserve = preserve or (sys.platform == "darwin" and not as_root)
        negative_flags = "-xpzf" if negative_preserve else "-xzf"
        subprocess.run(
            (["sudo", "-n"] if as_root else [])
            + ["tar", negative_flags, str(old), "-C", str(negative)],
            check=True,
            capture_output=True,
        )
        assert snapshot(negative) != prior
        # GNU tar without -p retains the existing sticky bit for non-root.
        expected_negative_mode = 0o755
        if sys.platform == "linux" and not as_root and not preserve:
            expected_negative_mode |= mode & stat.S_ISVTX
        assert stat.S_IMODE(negative.stat().st_mode) == expected_negative_mode
        if as_root:
            assert negative.stat().st_uid == negative.stat().st_gid == 4242
        evidence_dir = os.environ.get("HFC_ARCHIVE_EVIDENCE_DIR")
        if evidence_dir:

            def fields(values):
                return dict(zip(("inode", "uid", "gid", "mode"), values))

            evidence = {
                "platform": sys.platform,
                "root": as_root,
                "preserve": preserve,
                "destination_before": fields(before[destination][0]),
                "destination_after": fields(snapshot(destination)),
                "sentinels_unchanged": True,
                "negative_flags": negative_flags,
                "negative_before": fields(prior),
                "negative_after": fields(snapshot(negative)),
            }
            name = f"extraction-{sys.platform}-root{int(as_root)}-p{int(preserve)}-{mode:o}.json"
            (Path(evidence_dir) / name).write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
    finally:
        if as_root:
            # Only this test-created, identity-checked exclusive tree is touched.
            assert (
                snapshot(sandbox) == identity
                and sandbox.resolve().parent == tmp_path.resolve()
            )
            subprocess.run(
                [
                    "sudo",
                    "-n",
                    "chown",
                    "-R",
                    f"{os.getuid()}:{os.getgid()}",
                    str(sandbox),
                ],
                check=True,
            )


def test_cli_build_and_validate_actual_assets(tmp_path):
    output = tmp_path / "dist"
    command = [sys.executable, str(ROOT / "scripts/release_archives.py")]
    for action in ("build", "validate"):
        subprocess.run(
            command + [action, "--tag", TAG, "--output", str(output)],
            check=True,
            capture_output=True,
        )
    checksum = output / ("hermes-feishu-card-" + TAG + "-checksums.txt")
    checksum.write_text("wrong checksums")
    failed = subprocess.run(
        command + ["validate", "--tag", TAG, "--output", str(output)],
        check=False,
        capture_output=True,
    )
    assert failed.returncode != 0 and b"checksum manifest mismatch" in failed.stderr


@pytest.mark.parametrize("suffix", ["//", "/./"])
def test_validator_rejects_raw_noncanonical_directory_header(assets, tmp_path, suffix):
    source = next(a for a in assets if a.name.endswith("-linux.tar.gz"))
    data = bytearray(gzip.decompress(source.read_bytes()))
    top = ("hermes-feishu-card-" + TAG + "-linux" + suffix).encode()
    data[:100] = top.ljust(100, b"\0")
    data[148:156] = b"        "
    data[148:156] = f"{sum(data[:512]):06o}\0 ".encode()
    bad = tmp_path / "noncanonical.tar.gz"
    bad.write_bytes(gzip.compress(data))
    with pytest.raises(ArchiveError):
        validate_asset(bad, ROOT, TAG, "linux")


@pytest.mark.parametrize("platform", ["linux", "windows"])
@pytest.mark.parametrize("damage", ["name", "size"])
def test_bad_member_rejected_before_payload_read(
    assets, tmp_path, monkeypatch, platform, damage
):
    source = next(a for a in assets if ("-" + platform + ".") in a.name)
    bad = tmp_path / ("bad.zip" if platform == "windows" else "bad.tar.gz")
    if platform == "windows":
        with zipfile.ZipFile(source) as archive:
            member = archive.infolist()[1]
            content = archive.read(member)
        if damage == "name":
            member.filename = "../outside"
        else:
            content += b"x"
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr(member, content)

        def forbidden(*args, **kwargs):
            pytest.fail("invalid ZIP member payload must not be read")

        monkeypatch.setattr(zipfile.ZipFile, "read", forbidden)
    else:
        with tarfile.open(source) as archive:
            member = archive.getmembers()[1]
            content = archive.extractfile(member).read()
        if damage == "name":
            member.name = "../outside"
        else:
            content += b"x"
            member.size += 1
        with tarfile.open(bad, "w:gz") as archive:
            archive.addfile(member, io.BytesIO(content))

        def forbidden(*args, **kwargs):
            pytest.fail("invalid tar member payload must not be read")

        monkeypatch.setattr(tarfile.TarFile, "extractfile", forbidden)
    with pytest.raises(ArchiveError):
        validate_asset(bad, ROOT, TAG, platform)
