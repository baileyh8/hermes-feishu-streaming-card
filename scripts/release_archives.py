"""Build and verify the exact portable release assets (Python 3.9+, stdlib only)."""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import stat
import tarfile
import zipfile
from pathlib import Path

COMMON = (
    "README-install.md",
    "config.yaml.example",
    "README.md",
    "README.en.md",
    "CHANGELOG.md",
    "LICENSE",
    "install-docker.sh",
    "docker-compose.example.yml",
)
PLATFORMS = ("linux", "macos", "windows")


class ArchiveError(ValueError):
    """An asset does not match its release source and archive contract."""


def _manifest(source: Path, tag: str, platform: str):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag) or platform not in PLATFORMS:
        raise ArchiveError("invalid release tag or platform")
    top = "hermes-feishu-card-" + tag + "-" + platform
    result = {top: (None, 0o755)}
    for name in COMMON + (("install.ps1" if platform == "windows" else "install.sh"),):
        path = source / name
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ArchiveError("release source must be a regular file: " + name)
        result[top + "/" + name] = (
            path.read_bytes(),
            0o755 if name.endswith(".sh") else 0o644,
        )
    return result


def _asset_name(tag, platform):
    return (
        "hermes-feishu-card-"
        + tag
        + "-"
        + platform
        + (".zip" if platform == "windows" else ".tar.gz")
    )


def build_assets(source: Path, output: Path, tag: str):
    source, output = Path(source), Path(output)
    manifests = {platform: _manifest(source, tag, platform) for platform in PLATFORMS}
    output.mkdir(parents=True, exist_ok=True)
    assets = []
    for platform, manifest in manifests.items():
        asset = output / _asset_name(tag, platform)
        if platform == "windows":
            with zipfile.ZipFile(
                asset, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for name, (content, mode) in manifest.items():
                    info = zipfile.ZipInfo(
                        name + ("/" if content is None else ""), (1980, 1, 1, 0, 0, 0)
                    )
                    info.create_system = 3
                    info.external_attr = (
                        (stat.S_IFDIR if content is None else stat.S_IFREG) | mode
                    ) << 16
                    if content is None:
                        info.external_attr |= 0x10
                    archive.writestr(
                        info, content or b"", compress_type=zipfile.ZIP_DEFLATED
                    )
        else:
            with tarfile.open(asset, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
                for name, (content, mode) in manifest.items():
                    info = tarfile.TarInfo(name)
                    info.type = tarfile.DIRTYPE if content is None else tarfile.REGTYPE
                    info.mode = mode
                    info.size = len(content) if content is not None else 0
                    archive.addfile(
                        info, io.BytesIO(content) if content is not None else None
                    )
        validate_asset(asset, source, tag, platform)
        assets.append(asset)
    checksums = output / ("hermes-feishu-card-" + tag + "-checksums.txt")
    checksums.write_text(_checksums(assets), encoding="utf-8")
    return assets


def _checksums(assets):
    return "".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
        for path in sorted(assets)
    )


def validate_asset(asset: Path, source: Path, tag: str, platform: str):
    """Read without extraction; reject path, type, metadata, list or byte drift."""
    expected = _manifest(Path(source), tag, platform)
    seen = set()

    def check(name, content, mode):
        # Exact raw spelling matters: do not normalize traversal/absolute paths.
        if name in seen or name not in expected or expected[name] != (content, mode):
            raise ArchiveError(
                "duplicate, unexpected or changed archive member: " + name
            )
        seen.add(name)

    try:
        if platform == "windows":
            with zipfile.ZipFile(asset) as archive:
                for member in archive.infolist():
                    directory = member.is_dir()
                    name = member.filename[:-1] if directory else member.filename
                    mode = member.external_attr >> 16
                    wanted_type = stat.S_IFDIR if directory else stat.S_IFREG
                    if (
                        member.create_system != 3
                        or stat.S_IFMT(mode) != wanted_type
                        or member.flag_bits & 1
                        or member.extra
                        or member.comment
                        or member.orig_filename != member.filename
                        or member.date_time != (1980, 1, 1, 0, 0, 0)
                        or (directory and member.file_size != 0)
                    ):
                        raise ArchiveError("invalid ZIP member metadata")
                    if name in seen or name not in expected:
                        raise ArchiveError("duplicate or unexpected ZIP member")
                    content = expected[name][0]
                    if member.file_size != (len(content) if content is not None else 0):
                        raise ArchiveError("ZIP member size mismatch")
                    check(
                        name,
                        None if directory else archive.read(member),
                        stat.S_IMODE(mode),
                    )
        else:
            with tarfile.open(asset, "r:gz") as archive:
                for member in archive:
                    # tarfile folds trailing slashes and GNU/PAX overrides. Check
                    # the underlying USTAR header too, before accepting its view.
                    position = archive.fileobj.tell()
                    archive.fileobj.seek(member.offset)
                    header = archive.fileobj.read(512)
                    archive.fileobj.seek(position)
                    raw_name = header[:100].split(b"\0", 1)[0]
                    wanted_name = (
                        member.name + ("/" if member.isdir() else "")
                    ).encode("utf-8")
                    if (
                        raw_name != wanted_name
                        or header[345:500].strip(b"\0")
                        or header[156:157] != member.type
                    ):
                        raise ArchiveError("noncanonical raw tar header")
                    if (
                        member.type not in (tarfile.REGTYPE, tarfile.DIRTYPE)
                        or member.uid != 0
                        or member.gid != 0
                        or member.uname
                        or member.gname
                        or member.linkname
                        or member.pax_headers
                        or member.mtime != 0
                        or member.devmajor != 0
                        or member.devminor != 0
                        or (member.isdir() and member.size != 0)
                    ):
                        raise ArchiveError("invalid tar member metadata")
                    if member.name in seen or member.name not in expected:
                        raise ArchiveError("duplicate or unexpected tar member")
                    content = expected[member.name][0]
                    if member.size != (len(content) if content is not None else 0):
                        raise ArchiveError("tar member size mismatch")
                    stream = archive.extractfile(member) if member.isfile() else None
                    check(
                        member.name,
                        stream.read() if stream is not None else None,
                        member.mode,
                    )
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as error:
        raise ArchiveError("unreadable release archive") from error
    if seen != set(expected):
        raise ArchiveError("incomplete release archive")


def validate_assets(source: Path, output: Path, tag: str):
    assets = [output / _asset_name(tag, platform) for platform in PLATFORMS]
    for platform, asset in zip(PLATFORMS, assets):
        validate_asset(asset, source, tag, platform)
    checksum = output / ("hermes-feishu-card-" + tag + "-checksums.txt")
    if checksum.read_text(encoding="utf-8") != _checksums(assets):
        raise ArchiveError("release checksum manifest mismatch")
    if {p.name for p in output.iterdir()} != {p.name for p in assets + [checksum]}:
        raise ArchiveError("unexpected release output files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "validate"))
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    if args.action == "build":
        build_assets(args.source, args.output, args.tag)
    validate_assets(args.source, args.output, args.tag)
    print("release assets validated: " + args.tag)


if __name__ == "__main__":
    main()
