"""Precompute exact official-enable bytes using pinned upstream writer families.

This is a private-copy writer adapter, not a YAML merge implementation. Unknown
source, bindings, serializer paths or side effects refuse before real enable.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Any, Mapping

import yaml  # type: ignore[import-untyped]


class ConfigBytesRefused(ValueError):
    """The official command's exact config postimage cannot be proved."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "refused",
        stage: str = "plan",
        source: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", code) else "refused"
        self.stage = stage if re.fullmatch(r"[a-z][a-z0-9-]{0,31}", stage) else "plan"
        self.source = (
            source
            if source
            and len(source) <= 200
            and re.fullmatch(r"[A-Za-z0-9_./-]+", source)
            and not source.startswith("/")
            and ".." not in Path(source).parts
            else None
        )

    def diagnostic_label(self) -> str:
        label = f"{self.stage}:{self.code}"
        return f"{label}:{self.source}" if self.source else label


# Whole-source hashes bind cmd_enable's sequence, its imported helpers and the
# indirect serializer implementations. Hashes are stable across Python AST APIs.
# fixed: 3c27eb6234bf91b8ceee9e9071591b31e9b148cb
# pm: d923a5eb487a17c0a68801bba9ef0f56c81040a8
# pm-main: 0ab43d4f67e41be75f4f6c0fa14a86dbcfd3e5b3
FAMILIES: dict[str, dict[str, str]] = {
    "fixed": {
        "providers/__init__.py": "f1b572f3fe41efef5d8c6d7aeff1a24c0e9272dad6a6649019530d1bd0078818",
        "hermes_cli/plugins_cmd.py": "848a0ae1648d5e781706e6c0175d422899b08e8c730694bc69c1fc399d99d142",
        "hermes_cli/config.py": "0eca789d0351fa0f0f311934730629b76be6d1ff7b0182fb7bfa06fa79b2725c",
        "utils.py": "269375fff6ceea0976b43ead1b74083f68d3540c8d182d8d2bbcb421b82427c9",
    },
    "pm": {
        "hermes_cli/plugin_capabilities.py": "35cbdea310ceeb69829693f5077e30eafc739ade1923fc88fc9a06c66e56ab3e",
        "providers/__init__.py": "796d3ee0ed3aea06afc21525ec19dc39f17091845fa1e3f9be485acaa058423a",
        "hermes_cli/version_info.py": "2a422bbd2b32691d871b4fe550cd687be0bbb49989a76fedda8c00432d4944a9",
        "hermes_cli/plugins_cmd.py": "aec2fd443b48fba00b96308af8ba4ed139f2a7ab1a07770b1773028f021bd023",
        "hermes_cli/config.py": "a1667a958f52aa6201200bde354c3a380d79d49fd7635aeee3a3ebb23533a788",
        "utils.py": "394b44230f13ba608c6ce9b2d143563b2554a73a219567b03884637776a4859a",
        "hermes_yaml.py": "f97f0f08b6474c52434d0cffaa7ba0ecaa5b9279c4abcbb35c11d15f9c89db76",
        "hermes_cli/plugins_cmd_capabilities.py": "1322530460936f794e0b36cba11bafc87f592b7b0a9d713bb22bca9eb06d35f6",
        "pm/publication.py": "4f707e2a2d927c7e96a0327dadfe8af80c811dc288b7d992d2bc683e17860c2b",
        "pm/workspace.py": "a1a69386b2b7cc4eb2fd06d2f2c39a520913cadfee92fa282f1b25de0c1a58a2",
        "pm/plugins_state.py": "e4300eac3c00155b85cf6aba3c7b40faea05c67dde99385ff254a1345bebb5af",
        "hermes_cli/left_core_migration.py": "4899e985f2042954c0799ff2e12809aee78ee947133209eff88f076790df552c",
    },
    "pm-main": {
        "hermes_cli/plugin_capabilities.py": "35cbdea310ceeb69829693f5077e30eafc739ade1923fc88fc9a06c66e56ab3e",
        "providers/__init__.py": "89cb97d8fcb0ddcf97e47709d478bb49c959bb38e84dcfbe6a4f64ba58d44e5a",
        "hermes_cli/version_info.py": "2a422bbd2b32691d871b4fe550cd687be0bbb49989a76fedda8c00432d4944a9",
        "hermes_cli/plugins_cmd.py": "3ab3807bb4594e65fdba473cd035fb572b418f26d1f45249cff2fd1b9fee8ff4",
        "hermes_cli/config.py": "68239827c32c60114cc01d03a2c29f872c91d2114a3f0c1d1094de49980d0d2e",
        "utils.py": "9c4751dc9fbcbfe7e16d9e0cc5fb25566517c4927d2dc84061d4cac993a98445",
        "hermes_yaml.py": "f97f0f08b6474c52434d0cffaa7ba0ecaa5b9279c4abcbb35c11d15f9c89db76",
        "hermes_cli/plugins_cmd_capabilities.py": "1322530460936f794e0b36cba11bafc87f592b7b0a9d713bb22bca9eb06d35f6",
        "pm/publication.py": "4f707e2a2d927c7e96a0327dadfe8af80c811dc288b7d992d2bc683e17860c2b",
        "pm/workspace.py": "a1a69386b2b7cc4eb2fd06d2f2c39a520913cadfee92fa282f1b25de0c1a58a2",
        "pm/plugins_state.py": "e4300eac3c00155b85cf6aba3c7b40faea05c67dde99385ff254a1345bebb5af",
        "hermes_cli/left_core_migration.py": "78ae5dd2b5320a8402d5bb7f2bf29dfc7b2f3291d363ca425d77cafaccf972bb",
        "hermes_cli/config_worker_save.py": "d998581d802deb67eeef32d3fcbd7696f9d4a7a6c83f2bc83d6c8881c5d7dcf2",
    },
}


# Reviewed newer command topology: cmd_enable -> _activate_key ->
# plugins_admission/PM PluginSelection, then _enable_consent -> entry flag ->
# config.save_config -> config_worker_save -> utils round-trip serializer.
# HFC declares no capabilities or toolset; activation cannot add another config
# branch. Keep all prior families unchanged: the newer defaults and worker-save
# implementation are independent source contracts, even where output is equal.
# cf4f3a7396814ffcaef69d9af09f7cfdade3ddbe and
# f83b4df3ce1ab2ec62f1e0e90f22e0a1db21cb68 share pm-0223.
# 4d04303818b61bf3aa09278af48356315515965b: pm-0224.
# 4e58c721c8cf9b51c46636ea84ea6a5cb540880a: pm-main-4e58.
_PM_0224_WRITER = {
    'hermes_cli/plugins_cmd.py': 'ee3a38990478ca1e69666a126648be1d91bbe02891f5a8c405c543bfee30462d',
    'hermes_cli/config.py': '68239827c32c60114cc01d03a2c29f872c91d2114a3f0c1d1094de49980d0d2e',
    'utils.py': '9c4751dc9fbcbfe7e16d9e0cc5fb25566517c4927d2dc84061d4cac993a98445',
    'hermes_yaml.py': 'f97f0f08b6474c52434d0cffaa7ba0ecaa5b9279c4abcbb35c11d15f9c89db76',
    'hermes_cli/plugins_cmd_capabilities.py': '1322530460936f794e0b36cba11bafc87f592b7b0a9d713bb22bca9eb06d35f6',
    'hermes_cli/plugin_capabilities.py': '35cbdea310ceeb69829693f5077e30eafc739ade1923fc88fc9a06c66e56ab3e',
    'providers/__init__.py': '89cb97d8fcb0ddcf97e47709d478bb49c959bb38e84dcfbe6a4f64ba58d44e5a',
    'hermes_cli/version_info.py': '2a422bbd2b32691d871b4fe550cd687be0bbb49989a76fedda8c00432d4944a9',
    'pm/publication.py': '4f707e2a2d927c7e96a0327dadfe8af80c811dc288b7d992d2bc683e17860c2b',
    'pm/workspace.py': 'a1a69386b2b7cc4eb2fd06d2f2c39a520913cadfee92fa282f1b25de0c1a58a2',
    'pm/plugins_state.py': 'e4300eac3c00155b85cf6aba3c7b40faea05c67dde99385ff254a1345bebb5af',
    'hermes_cli/left_core_migration.py': '78ae5dd2b5320a8402d5bb7f2bf29dfc7b2f3291d363ca425d77cafaccf972bb',
    'hermes_cli/config_worker_save.py': 'a12df826b8d5b10c978ccaf45d59f11513852764b10eb07ace3d5a314c9a4f9b',
    'hermes_cli/plugins_admission.py': '57426311fb5a6dce6b785f3ca5bd15823f394bde7480212dc5bbc1b30278efbe',
    'pm/plugin_inputs.py': '5804dec5716ef7763aef37dab96183aca236500254cb574c0f591f167db430e2',
    'hermes_cli/config_defaults.py': '93c989bcd0a4c3aff116ad2534375ece02bce4b79b9e9166dfacf834f09bc63b',
    'hermes_cli/config_providers.py': '9fb0cab242c7d76c878a9c1fec238c8247567b1d027a71f34a326197268cd075',
    'hermes_cli/config_read_errors.py': '1a623d28712f7f264af2bed8c132f642f7e96a67def3c6380fc1df316d91de7b',
    'hermes_cli/config_version_stamp.py': '4cf5c3b74f429424fd1284a31de586669518c783d6cc0889562044df53d7046d',
    'hermes_cli/managed_scope.py': 'd048bb14fae4517d9c0dc90255c9b495fdba3bbf5311a50aa5108b43c724874d',
    'hermes_constants.py': '870ad9935a62968636d6a14d160c0269dfbefd8091ab3cb71be78b30bef6360f',
    'hermes_cli/route_identity.py': '26030db2efe4c4566a9276c1aaf5b31ed67d0e6734f0b2b53985d612d13c36d2',
    'hermes_cli/relay_plugin_cutover.py': '2e52dd1aab1e2dabe8ccac9e7cc58d15d4813cc1ee494884b1c3b30e7329863a',
    'hermes_cli/plugins_activation.py': '72f6bc206eadec58c922156cd35a8b8dd13eb0d1da9bcca6c61e1230b06d4349',
    'agent/safe_worker_policy.py': 'f0759f0868f402d5aff6007303ad503e3d3d92999ed01693037bc8f07b4a2738',
    'agent/secret_scope.py': 'dce64ce99a6966836108e202e1db41c4be97bd3dad41a79e357f39b8d7e90dec',
    'hermes_cli/config_defaults_env.py': '1fb00b0930722327b35d37312894decfca0a9f9db2e55d5391cf0305f5cd3829',
    'hermes_cli/moa_config.py': 'e75d7d6f1c4f56c5add9c0b9055596e0d95c769fa58364e5f2e364d67780c0df',
    'hermes_cli/config_home.py': '4d196f99c7e64518283df4d49baa9a46f47727aaf9b9c54b96c56734e6528dae',
    'hermes_cli/config_backups.py': 'a6bf35432765b3281bc19f0d400b81b4fe1f34a0b83e9c7cebde0aa7946dc516',
    'hermes_dir_acl.py': 'd978e1fbddffd05f4506e9c6d80a279ac9d5fea799d9a3174b260a7ff02bb50e',
    'hermes_cli/observability/shared_metrics_disabled.py': '35ea0f819ececf5471795a9af3124f14e0d6316b6683ef97823167857e2de663',
    'hermes_cli/toolset_validation.py': '417def17fc7bbed252b388840d85519474250c6d8c92fd071463035c2e830df4',

}

# v0.22.5 keeps the enable/write sequence, but changes six files on that path:
# plugins_cmd only routes the post-enable notification through report_activation;
# config and left_core_migration add lazy terminal-key migration reads; config_defaults
# changes data defaults; pm/workspace changes the workspace-member predicate; and
# plugins_activation adds gateway detection.  The private planner still writes the
# selected lists and the entry flag directly, so bind the changed sources as a
# separate family rather than accepting v0.22.5 as pm-0224.
_PM_0225_WRITER = {
    **_PM_0224_WRITER,
    'hermes_cli/plugins_cmd.py': '4944559cbdec88a4bd08edd24faad47a921e768b04c8cb7a171e2634494d08fb',
    'hermes_cli/config.py': '1aebd87e71338c17f63bdf13ecd62c59aa053bc0b1270398efaa327642a1142b',
    'pm/workspace.py': 'ecc8c9ccd38ddfbfe6cd228b727e4e13e83765f0166bc2e3ebd21d1582e6e506',
    'hermes_cli/left_core_migration.py': '197c21fe68d24f0f6df519fcbed22b6d6be8e3fe85765e0663c50efc06b9a05d',
    'hermes_cli/config_defaults.py': '64890345b7b4d6601bd0dd4c8caac95ba1eaf793ae8b9236c0ccf149e6b2b3bf',
    'hermes_cli/plugins_activation.py': '83450aea21cbe7214b06cfe873c7f2b2cd84f587d22cad0660e761e8bcbe52e0',
    # Local modules executed by the bounded private planner import path.
    # No provider/plugin discovery or third-party dependency closure is admitted.
    'agent/__init__.py': '21cdce89e9ffdb847634bf498b087410cd191a1e375db43928735846fb7ddc86',
    'agent/i18n.py': '818d6f6d2870f8931b7a4d2f4583f86194bfc733f5b8fed63627437104917ac8',
    'agent/i18n_languages.py': 'ec22e409f7a964250cc9f67f317196623f5aa2ad86f23287685e9aa060910a49',
    'agent/i18n_layers.py': 'f77fc370c92522f5f1d7b7b14de1513e43d25ac6fef73601de48c074a61a5670',
    'agent/jiter_preload.py': 'bb66461d6e52e54dd2368ad021304d74676824924c45df80f0a3e769c9e5fff3',
    'hermes_cli/__init__.py': '6a4ab05b8621ec1cd66a5abbe30fa32ed5bddddea95903036da01f0af1af94ce',
    'hermes_cli/_subprocess_compat.py': '4d036c56de8065099f4b82a50fde9095c43dab7e851e0e3a3fe4cd5c143a212e',
    'hermes_cli/cli_output.py': 'ebf4ddc96fb90fd21d7c2dd2805e49ae7ecf280148ee15dd531b3efaa1251e8e',
    'hermes_cli/colors.py': '12d4cd7deaa3832a73b7758609cc9607871a9aa222c5423ca6c0f3e3d4cc2ee6',
    'hermes_cli/default_soul.py': 'b65192a631f0011fcef245c94d9741e0c716defe605d3edfd0e59f26da211d2d',
    'hermes_cli/observability/__init__.py': 'ff9ef5f81f6533b011115b69ebcafe65fd3276877fa614b431385115681bb15b',
    'hermes_cli/personality.py': '9e1f7b44758704c332e893186b15c2be6cf90f2989f7654ae2b46916e5979f8d',
    'hermes_cli/plugin_install_phase.py': 'e4b98d59c50ed01e0daaa47fd2a13216349c7d2e963de1ad9aa5648a9b05bd81',
    'hermes_cli/plugins_cmd_git.py': 'e0a71fad2f8a3fc33b80ecff9e6afff00f59df60b57504e4b317927412588f38',
    'hermes_cli/plugins_cmd_install.py': '8137442dbdfef7d6025da5fcdcf98a4a739c173583acc08241efcf89179576f0',
    'hermes_cli/plugins_cmd_listing.py': '9f8ab0eb85f73dcf1d8e6acc8b270a38592789d30ae125299698e82a8726e7fc',
    'hermes_cli/plugins_cmd_remove.py': 'bdb5cbc176d53c24f95c22ae1e98926b9568eff0486e24ce2d2dae20c9b72c3b',
    'hermes_cli/plugins_cmd_toggle.py': '561054775f12f146417c4cf5cd5e3c6cffac421734c5211af0026da3d0ad4095',
    'hermes_cli/plugins_cmd_update.py': '52219c30344d686d485f72e11a26c196785b80defd24270da144cfaa34e2b567',
    'hermes_cli/secret_prompt.py': 'd523702f8899ee5b42eed38e45883c871bde991a070cd40304f8c82186966356',
    'hermes_cli/stale_modules.py': '35625a6fe429f1b5555e200aa47e84fe73d01ae2815ff53d3c4fa8e56b575eb2',
    'hermes_platform/__init__.py': '698ba6987eb24da01e63c336607529af9cd38f8694c58f7b96e484b22cbdf5a6',
    'hermes_platform/host/__init__.py': 'af071e6da9cf17505724bea27a75798f4ec6cd296a8e9821b0bab11bcc06e55b',
    'hermes_platform/host/darwin_pci.py': '2ecbf4c68873f16f6de855794503c3ba781e3f26ea8da910a8ce5cd513f96fcb',
    'hermes_platform/host/facts.py': '52eef216017ca8e18d7365ec10a9494b2d261280cfc70d9e0b94a2cc7b363d7a',
    'hermes_platform/host/runtime.py': 'fd760a3f044d6201aa5b543d0166de11f27e2da6a700839623ebd67ce1037d6a',
    'providers/base.py': 'b948b1369e80690fe36bbedef5c5d4871c748bc34a0e31a64fc012fb5c99f0b1',

}
FAMILIES["pm-0223"] = {
    **_PM_0224_WRITER,
    "hermes_cli/config_defaults.py": "615284fcd701fa88d684852a365cc06755e9e348576dd00f83a258e8addee316",
}
FAMILIES["pm-0224"] = dict(_PM_0224_WRITER)
FAMILIES["pm-0225"] = dict(_PM_0225_WRITER)
# v0.22.1 predates the v0.22.2 writer split for plugins_cmd and utils, while
# already sharing the PM publication and worker-save path with pm-main.  Keep
# its two old sources explicit so neither the legacy pm family nor pm-main can
# silently stand in for this revision.
FAMILIES["pm-0221"] = {
    **FAMILIES["pm-main"],
    "hermes_cli/plugins_cmd.py": "aec2fd443b48fba00b96308af8ba4ed139f2a7ab1a07770b1773028f021bd023",
    "utils.py": "394b44230f13ba608c6ce9b2d143563b2554a73a219567b03884637776a4859a",
}
FAMILIES["pm-main-4e58"] = {
    **_PM_0224_WRITER,
    "hermes_cli/left_core_migration.py": "a1a67f61f5d264d5cf935f8acb2a14967ad06f4efca3ce3441f4c676950b25dc",
}


# The official launch bootstrap executes these files before the writer plan.
# They are intentionally separate from FAMILIES: the writer contract and the
# launcher contract have different consumers and must not imply one another.
_PM_INIT = "6a4ab05b8621ec1cd66a5abbe30fa32ed5bddddea95903036da01f0af1af94ce"
LAUNCH_SOURCES: dict[str, dict[str, str]] = {
    "fixed": {
        "hermes_cli/main.py": "f27fcee078bc3696d4c37fa98d04972e214d53b01bd6075f12e988ef69d6c241",
        "hermes_cli/__init__.py": "e4c421279251971d07f84628d1d59222f0368ab297ce8fd0dff10e5938fe971e",
    },
    "pm": {
        "hermes_cli/main.py": "88a7fc166d3bfaac082d44bbfc36c93b53c50f7508ca96e32206dc3f1edfaa82",
        "hermes_cli/__init__.py": _PM_INIT,
    },
    "pm-main": {
        "hermes_cli/main.py": "9b3c4c7ce4626c414f2f9b63338a3a6480eac5bb2028d53bbfb816110ce47ed9",
        "hermes_cli/__init__.py": _PM_INIT,
    },
    "pm-0223": {
        "hermes_cli/main.py": "244637472a5fdad080c99f99356d5acd7fc99bbf8b61cab153a2eb580a61a688",
        "hermes_cli/__init__.py": _PM_INIT,
    },
    "pm-0221": {
        "hermes_cli/main.py": "9ef03ed61d0b6949e47bf2617997cda83b62dc6f007abc7813625cf74f21efde",
        "hermes_cli/__init__.py": _PM_INIT,
    },
    "pm-0224": {
        "hermes_cli/main.py": "244637472a5fdad080c99f99356d5acd7fc99bbf8b61cab153a2eb580a61a688",
        "hermes_cli/__init__.py": _PM_INIT,
    },
    "pm-0225": {
        "hermes_cli/main.py": "95635aea97728e2759f9158210ae60fcfb7141610f9e7cbc2182021d9a50d416",
        "hermes_cli/__init__.py": _PM_INIT,
    },
    "pm-main-4e58": {
        "hermes_cli/main.py": "b822f3aa776d7f94eef646309a6d64cf4b4ea53f6bf5c02d4b06ef17c3d3080f",
        "hermes_cli/__init__.py": _PM_INIT,
    },
}


def _digest(path: Path) -> str:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > 2 * 1024 * 1024:
            raise ConfigBytesRefused("official config writer source is unsafe")
        value = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise ConfigBytesRefused(
            "official config writer source is unavailable"
        ) from exc
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ConfigBytesRefused("official config writer source changed")
    return hashlib.sha256(value).hexdigest()


def _is_reparse(metadata: os.stat_result) -> bool:
    """Windows junctions can report directory mode without symlink mode."""
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def verify_family(root: Path, family: str) -> None:
    if family not in FAMILIES or any(
        _digest(root / name) != digest for name, digest in FAMILIES[family].items()
    ):
        raise ConfigBytesRefused("official config writer contract is unknown")


def _launch_path(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ConfigBytesRefused(
            "official launch source path is invalid",
            code="launch-path",
            stage="launch",
        )
    current = root
    for part in Path(relative).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ConfigBytesRefused(
                "official launch source is unavailable",
                code="launch-source-unavailable",
                stage="launch",
                source=relative,
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ConfigBytesRefused(
                "official launch source is a symlink",
                code="launch-source-symlink",
                stage="launch",
                source=relative,
            )
        if _is_reparse(metadata):
            raise ConfigBytesRefused(
                "official launch source is a reparse point",
                code="launch-source-reparse",
                stage="launch",
                source=relative,
            )
        if current != root / Path(relative) and not stat.S_ISDIR(metadata.st_mode):
            raise ConfigBytesRefused(
                "official launch source parent is not a directory",
                code="launch-source-parent",
                stage="launch",
                source=relative,
            )
    return current


def _verify_launch_root(root: Path) -> Path:
    try:
        absolute = Path(os.path.abspath(os.fspath(root)))
        root_metadata = absolute.lstat()
    except OSError as exc:
        raise ConfigBytesRefused(
            "official launch root is unavailable",
            code="launch-root-unavailable",
            stage="launch",
        ) from exc
    if stat.S_ISLNK(root_metadata.st_mode):
        raise ConfigBytesRefused(
            "official launch root is a symlink",
            code="launch-root-symlink",
            stage="launch",
        )
    if _is_reparse(root_metadata):
        raise ConfigBytesRefused(
            "official launch root is a reparse point",
            code="launch-root-reparse",
            stage="launch",
        )
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ConfigBytesRefused(
            "official launch root is not a directory",
            code="launch-root-type",
            stage="launch",
        )
    for ancestor in absolute.parents:
        try:
            metadata = ancestor.lstat()
        except OSError as exc:
            raise ConfigBytesRefused(
                "official launch ancestor is unavailable",
                code="launch-ancestor-unavailable",
                stage="launch",
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ConfigBytesRefused(
                "official launch ancestor is a symlink",
                code="launch-ancestor-symlink",
                stage="launch",
            )
        if _is_reparse(metadata):
            raise ConfigBytesRefused(
                "official launch ancestor is a reparse point",
                code="launch-ancestor-reparse",
                stage="launch",
            )
    return absolute


def verify_launch_sources(root: Path, *, family: str | None = None) -> str:
    """Prove the exact launcher files before executing the official CLI.

    The launch bootstrap runs ``hermes_cli.main`` directly, so writer-source
    verification alone is insufficient.  Root and every target component must
    be non-symlinks; both launch files must be regular files with their exact
    reviewed bytes.  The return value is the selected family.
    """
    absolute = _verify_launch_root(root)
    if family is None:
        for candidate in FAMILIES:
            try:
                verify_family(absolute, candidate)
            except ConfigBytesRefused:
                continue
            family = candidate
            break
    if family is None or family not in FAMILIES:
        raise ConfigBytesRefused(
            "official config writer contract is unknown",
            code="launch-family-unknown",
            stage="launch",
        )
    expected = LAUNCH_SOURCES.get(family)
    if expected is None:
        raise ConfigBytesRefused(
            "official launch source contract is unknown",
            code="launch-source-unknown",
            stage="launch",
        )
    for relative, digest in expected.items():
        path = _launch_path(absolute, relative)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ConfigBytesRefused(
                "official launch source is unavailable",
                code="launch-source-unavailable",
                stage="launch",
                source=relative,
            ) from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigBytesRefused(
                "official launch source is not a regular file",
                code="launch-source-type",
                stage="launch",
                source=relative,
            )
        if _is_reparse(metadata):
            raise ConfigBytesRefused(
                "official launch source is a reparse point",
                code="launch-source-reparse",
                stage="launch",
                source=relative,
            )
        try:
            actual = _digest(path)
        except ConfigBytesRefused as exc:
            raise ConfigBytesRefused(
                "official launch source is unsafe",
                code="launch-source-unsafe",
                stage="launch",
                source=relative,
            ) from exc
        if actual != digest:
            raise ConfigBytesRefused(
                "official launch source changed",
                code="launch-source-drift",
                stage="launch",
                source=relative,
            )
    verify_family(absolute, family)
    return family


def _mapping(value: bytes) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(value.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ConfigBytesRefused(
            "official config byte plan is invalid YAML",
            code="invalid-yaml",
            stage="verify",
        ) from exc
    if parsed is None:
        return {}
    if type(parsed) is not dict or any(type(key) is not str for key in parsed):
        raise ConfigBytesRefused(
            "official config byte plan must be a string-keyed mapping",
            code="invalid-root",
            stage="verify",
        )
    return parsed


def _schema_version(mapping: Mapping[str, Any]) -> int | None:
    if "_config_version" not in mapping:
        return None
    version = mapping.get("_config_version")
    if type(version) is not int or version < 0:
        raise ConfigBytesRefused(
            "official config byte plan has an invalid schema version",
            code="invalid-schema-version",
            stage="verify",
        )
    return version


@dataclass(frozen=True, repr=False)
class ConfigBytesPlan:
    postimage: bytes
    root: Path
    family: str
    config_version: int | None = None

    def verify_source(self) -> None:
        verify_family(self.root, self.family)

    def verify_launch_sources(self) -> None:
        verify_launch_sources(self.root, family=self.family)

    def matches_expected(self, expected: Mapping[str, Any]) -> bool:
        """Match config semantics while allowing only the writer's exact stamp.

        The official writer may stamp the schema version as part of every save.
        That metadata update is accepted only when it equals the value produced
        by this verified plan and does not move an existing stamp backwards.
        Every other mapping field must match exactly.  Callers still compare the
        complete postimage bytes after running the official command.
        """
        if not isinstance(expected, Mapping):
            return False
        try:
            actual = _mapping(self.postimage)
            expected_mapping = dict(expected)
        except (ConfigBytesRefused, TypeError, ValueError):
            return False
        try:
            actual_version = _schema_version(actual)
        except ConfigBytesRefused:
            return False
        if actual_version != self.config_version:
            return False
        if actual == expected_mapping:
            return True
        if self.config_version is None:
            return False
        if "_config_version" in expected_mapping:
            expected_version = expected_mapping.get("_config_version")
            if type(expected_version) is not int or expected_version < 0:
                return False
            if expected_version > self.config_version:
                return False
        actual_without_stamp = dict(actual)
        expected_without_stamp = dict(expected_mapping)
        actual_without_stamp.pop("_config_version", None)
        expected_without_stamp.pop("_config_version", None)
        return actual_without_stamp == expected_without_stamp


_CHILD = r"""
import hashlib,importlib,json,os,stat,sys,sysconfig
from pathlib import Path
sys.dont_write_bytecode=True
root=Path(sys.argv[1]).resolve();home=Path(sys.argv[2]).resolve()
request=json.loads((home/'request.json').read_text())
real_home=Path(request['real_home']).resolve()
# Imports may read selected source/runtime libraries. All writes and all config
# reads are confined to this private HOME; no resolver subprocess or transport.
def under(path, base):
    return path==base or base in path.parents
def deny(reason,stage='audit',source=None):
    report={'reason':reason,'stage':stage}
    if source: report['source']=source
    sys.stderr.write('HFC_CONFIG_PLAN_DENY '+json.dumps(report,sort_keys=True,separators=(',',':'))+'\n')
    sys.stderr.flush()
    os._exit(73)
def reparse(metadata):
    return bool(getattr(metadata,'st_file_attributes',0)
                & getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400))
def nonsymlink_path(value):
    path=Path(os.path.abspath(os.fspath(value)))
    for part in (path,*path.parents):
        try: metadata=part.lstat()
        except OSError: return False
        if stat.S_ISLNK(metadata.st_mode) or reparse(metadata): return False
    return True
def has_symlink(value):
    path=Path(os.path.abspath(os.fspath(value)))
    for part in (path,*path.parents):
        try: metadata=part.lstat()
        except FileNotFoundError: continue
        except OSError: return True
        if stat.S_ISLNK(metadata.st_mode) or reparse(metadata): return True
    return False
library_roots=[]
for key in ('stdlib','platstdlib','purelib','platlib'):
    value=sysconfig.get_paths().get(key)
    if not value: continue
    raw=Path(os.path.abspath(value))
    base=raw.resolve()
    raw_under_root=under(raw,root)
    base_under_root=under(base,root)
    root_under_base=under(root,base)
    if raw_under_root or base_under_root or root_under_base:
        if base==root or base in root.parents: deny('library-root-overlap','library')
        if not nonsymlink_path(raw) or not nonsymlink_path(base):
            deny('library-root-symlink','library')
        if raw_under_root != base_under_root:
            deny('library-root-overlap','library')
        for package in ('hermes_cli','providers','agent','hermes_platform','pm'):
            package_root=root/package
            if base==package_root or package_root in base.parents:
                deny('library-root-overlap','library')
    if base not in library_roots: library_roots.append(base)
def library_path(path):
    return any(under(path,base) for base in library_roots)
def audit(event,args):
    if request['family']=='pm-0225' and event=='exec':
        filename=args[0].co_filename
        if not filename.startswith('<'):
            raw=Path(os.path.abspath(os.fspath(filename)))
            if under(raw,root):
                if not nonsymlink_path(raw):
                    deny('local-source-symlink','exec',raw.relative_to(root).as_posix())
                path=raw.resolve()
                if not library_path(path) and path.relative_to(root).as_posix() not in request['sources']:
                    deny('unbound-local-source','exec',path.relative_to(root).as_posix())
    if event in {'subprocess.Popen','os.system','socket.connect','socket.bind'}:
        if event=='subprocess.Popen':
            frame=sys._getframe(1)
            while frame and frame.f_globals.get('__name__')=='subprocess': frame=frame.f_back
            # The pinned writer's optional telemetry imports version metadata.
            # Its git lookup is irrelevant to bytes and catches this refusal;
            # block it without starting a subprocess. The entire source is bound.
            if frame.f_globals.get('__name__')=='hermes_cli.version_info' and frame.f_code.co_name=='_run_git':
                raise RuntimeError('version metadata subprocess disabled in shadow')
            deny('subprocess','process')
        deny('network' if event.startswith('socket.') else 'process',event)
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        raw=Path(os.path.abspath(os.fsdecode(args[0])))
        if under(raw,root) and has_symlink(raw):
            deny('local-source-symlink','open',raw.relative_to(root).as_posix())
        path=raw.resolve()
        # Ignore checkout bytecode caches: verified source must be compiled.
        if request['family']=='pm-0225' and under(path,root) and path.suffix=='.pyc' and not library_path(path):
            raise OSError('private planner requires verified source')
        mode=args[1];flags=args[2]
        writing=(isinstance(mode,str) and any(x in mode for x in 'wax+')) or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
        if writing and not under(path,home): deny('shadow-write','open')
        if path in {real_home/'config.yaml',real_home/'.env'}: deny('real-config-read','open')
    if event in {'os.mkdir','os.remove','os.rmdir','os.chmod'}:
        if isinstance(args[0],int):
            identity=os.fstat(args[0])
            if not any((p.stat().st_dev,p.stat().st_ino)==(identity.st_dev,identity.st_ino)
                       for p in home.rglob('*') if p.is_file() and not p.is_symlink()):
                deny('fd-outside-shadow',event)
            return
        path=Path(os.fsdecode(args[0])).resolve()
        if not under(path,home): deny('shadow-mutation',event)
    if event in {'os.rename','os.replace'}:
        if not all(under(Path(os.fsdecode(p)).resolve(),home) for p in args[:2]):
            deny('rename-outside-shadow',event)
sys.addaudithook(audit)
def profile(frame,event,arg):
    if event!='call': return
    module=frame.f_globals.get('__name__','') or ''
    name=frame.f_code.co_name
    if ((module.startswith('hermes_cli.plugins') and name in {
        'discover_plugins','discover_and_load','_discover_and_load_inner','_load_plugin','_load_plugin_scoped'})
        or (module.startswith('hermes_feishu_card') and name=='bootstrap_plugin_runtime')
        or (module=='providers' and name in {'_discover_providers','_run_discovery_steps','_import_plugin_dir','_discover_entry_point_providers'})
        or (module in {'pm.install','pm.client','pm._uv'} and name!='<module>')):
        deny('plugin-load','profile')
sys.setprofile(profile)
for name,digest in request['sources'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
sys.path.insert(0,str(root))
# Module origins must agree with the inspected checkout. Same interpreter and
# restricted environment as the real official command; no inherited PYTHONPATH.
def module(name,relative):
    value=importlib.import_module(name)
    assert Path(value.__file__).resolve()==root/relative
    return value
providers=module('providers','providers/__init__.py')
list_providers=providers.list_providers
def no_setup_provider_metadata(*args,**kwargs):
    caller=sys._getframe(1)
    if caller.f_globals.get('__name__')=='hermes_cli.config' and caller.f_code.co_name=='_inject_profile_env_vars':
        raise RuntimeError('setup prompt metadata is excluded from byte planning')
    deny('provider-metadata','profile')
providers.list_providers=no_setup_provider_metadata
config=module('hermes_cli.config','hermes_cli/config.py')
providers.list_providers=list_providers
utils=module('utils','utils.py')
desired=request['desired']
commands=module('hermes_cli.plugins_cmd','hermes_cli/plugins_cmd.py')
selection_needed=not request['enabled_before'] or (request['family']!='fixed' and 'hermes-feishu-card' in config.read_user_config_raw().get('plugins',{}).get('disabled',[]))
if selection_needed:
    if request['family']=='fixed':
        commands._save_enabled_set(set(desired['plugins']['enabled']))
        commands._save_disabled_set(set(desired['plugins']['disabled']))
    else:
        yaml=module('hermes_yaml','hermes_yaml.py').roundtrip_yaml()
        state=yaml.load((home/'config.yaml').read_bytes().decode('utf-8-sig')) or {}
        section=state.setdefault('plugins',{})
        section['enabled']=desired['plugins']['enabled']
        section['disabled']=desired['plugins']['disabled']
        import io
        stream=io.StringIO();yaml.dump(state,stream)
        (home/'config.yaml').write_bytes(stream.getvalue().encode('utf-8'))
grant=commands if request['family']=='fixed' else module('hermes_cli.plugins_cmd_capabilities','hermes_cli/plugins_cmd_capabilities.py')
grant._set_plugin_entry_flag('hermes-feishu-card','allow_tool_override',False)
assert not any(name in sys.modules for name in ('pm.install','pm.client'))
for name,digest in request['sources'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
assert (home/'config.yaml').is_file()
"""


def _child_refusal(stderr: bytes | str | None, returncode: int) -> ConfigBytesRefused:
    """Read only the child's bounded audit envelope; discard all other output."""
    raw = stderr or b""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="replace")
    prefix = b"HFC_CONFIG_PLAN_DENY "
    for line in raw.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            payload = json.loads(line[len(prefix):].decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if type(payload) is not dict or not {"reason", "stage"} <= set(payload):
            continue
        if set(payload) - {"reason", "stage", "source"}:
            continue
        reason, stage = payload.get("reason"), payload.get("stage")
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", reason):
            continue
        if not isinstance(stage, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", stage):
            continue
        source = payload.get("source")
        if source is not None and not isinstance(source, str):
            source = None
        return ConfigBytesRefused(
            "official config byte plan failed",
            code=reason,
            stage=stage,
            source=source,
        )
    return ConfigBytesRefused(
        "official config byte plan failed",
        code="audit-refused" if returncode == 73 else "child-exit",
        stage="audit" if returncode == 73 else "child",
    )


def plan_enabled_bytes(
    *,
    root: Path,
    python: Path,
    home: Path,
    state_dir: Path,
    before: bytes,
    desired: Mapping[str, Any],
    enabled_before: bool,
) -> ConfigBytesPlan:
    family = None
    for candidate in FAMILIES:
        try:
            verify_family(root, candidate)
        except ConfigBytesRefused:
            continue
        family = candidate
        break
    if family is None:
        raise ConfigBytesRefused("official config writer contract is unknown")
    try:
        with tempfile.TemporaryDirectory(
            prefix="hfc-config-plan-", dir=state_dir
        ) as scratch_name:
            # Resolve the private paths before the audit compares them.  On
            # macOS, /var is a symlink to /private/var; leaving one side
            # unresolved makes every legitimate shadow write look external.
            scratch = Path(scratch_name).resolve()
            shadow = (scratch / "home").resolve()
            shadow.mkdir(mode=0o700)
            os.chmod(scratch, 0o700)
            for name, value in {
                "config.yaml": before,
                "request.json": json.dumps(
                    {
                        "real_home": str(home),
                        "family": family,
                        "sources": FAMILIES[family],
                        "desired": desired,
                        "enabled_before": enabled_before,
                    }
                ).encode(),
            }.items():
                path = shadow / name
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(value)
            child = subprocess.run(
                [str(python), "-I", "-c", _CHILD, str(root), str(shadow)],
                cwd=root,
                env={"HOME": str(shadow), "HERMES_HOME": str(shadow)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            if child.returncode != 0:
                raise _child_refusal(child.stderr, child.returncode)
            postimage = (shadow / "config.yaml").read_bytes()
    except subprocess.TimeoutExpired as exc:
        raise ConfigBytesRefused(
            "official config byte plan timed out",
            code="timeout",
            stage="child",
        ) from exc
    except subprocess.SubprocessError as exc:
        raise ConfigBytesRefused(
            "official config byte plan failed",
            code="child-process",
            stage="child",
        ) from exc
    except OSError as exc:
        # Never forward stderr, arbitrary exception text or config bytes.
        raise ConfigBytesRefused(
            "official config byte plan failed",
            code="child-io",
            stage="child",
        ) from exc
    verify_family(root, family)
    mapping = _mapping(postimage)
    return ConfigBytesPlan(postimage, root, family, _schema_version(mapping))
