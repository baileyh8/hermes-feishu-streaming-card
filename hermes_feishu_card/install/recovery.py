from __future__ import annotations

import ast
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Dict, Optional, Tuple

from .detect import HermesDetection
from .patcher import (
    apply_cron_patch,
    apply_patch,
    remove_cron_patch,
    remove_patch,
)


BACKUP_SUFFIX = ".hermes_feishu_card.bak"
MANIFEST_NAME = ".hermes_feishu_card_manifest"
KNOWN_STATES = {
    "clean",
    "installed",
    "stale_unpatched",
    "owned_incomplete",
    "corrupt_owned",
    "refused",
}

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_STATUS_PREFIX = "!recovery:"
_MANIFEST_ERROR = "_recovery_error"


@dataclass(frozen=True)
class RecoveryFinding:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class RecoveryEvidence:
    current_text: str
    current_sha256: str
    backup_text: Optional[str]
    backup_sha256: str
    manifest: Optional[Dict[str, object]]
    marker_error: str
    cron_current_text: Optional[str]
    cron_current_sha256: str
    cron_backup_text: Optional[str]
    cron_backup_sha256: str
    cron_marker_error: str


@dataclass(frozen=True)
class RecoveryClassification:
    state: str
    executable: bool
    fingerprint_parts: Dict[str, str]
    actions: Tuple[str, ...]
    findings: Tuple[RecoveryFinding, ...]


@dataclass(frozen=True)
class RecoveryPlan:
    root: Path
    state: str
    executable: bool
    fingerprint: str
    actions: Tuple[str, ...]
    findings: Tuple[RecoveryFinding, ...]


def _fingerprint(parts: Dict[str, str]) -> str:
    encoded = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(encoded).hexdigest()


def _read_evidence(detection: HermesDetection) -> RecoveryEvidence:
    run_py = detection.run_py
    backup_path = run_py.with_name(f"{run_py.name}{BACKUP_SUFFIX}")
    manifest_path = detection.root / MANIFEST_NAME
    manifest = _read_manifest_evidence(manifest_path)
    (
        cron_current_text,
        cron_current_sha256,
        cron_backup_text,
        cron_backup_sha256,
        cron_marker_error,
    ) = _read_cron_evidence(detection)

    if run_py.is_symlink():
        return RecoveryEvidence(
            current_text="",
            current_sha256="",
            backup_text=None,
            backup_sha256="",
            manifest=manifest,
            marker_error="symlink_refused",
            cron_current_text=cron_current_text,
            cron_current_sha256=cron_current_sha256,
            cron_backup_text=cron_backup_text,
            cron_backup_sha256=cron_backup_sha256,
            cron_marker_error=cron_marker_error,
        )

    try:
        current_text = _read_text(run_py)
    except (OSError, UnicodeError):
        return RecoveryEvidence(
            current_text="",
            current_sha256="",
            backup_text=None,
            backup_sha256="",
            manifest=manifest,
            marker_error="current_read_error",
            cron_current_text=cron_current_text,
            cron_current_sha256=cron_current_sha256,
            cron_backup_text=cron_backup_text,
            cron_backup_sha256=cron_backup_sha256,
            cron_marker_error=cron_marker_error,
        )

    marker_error = ""
    try:
        remove_patch(current_text)
    except ValueError:
        marker_error = "corrupt_patch_markers"

    backup_text: Optional[str] = None
    backup_sha256 = ""
    if backup_path.is_symlink():
        backup_sha256 = f"{_STATUS_PREFIX}symlink"
    elif backup_path.exists():
        try:
            backup_text = _read_text(backup_path)
            backup_sha256 = _text_sha256(backup_text)
        except (OSError, UnicodeError):
            backup_sha256 = f"{_STATUS_PREFIX}read_error"

    return RecoveryEvidence(
        current_text=current_text,
        current_sha256=_text_sha256(current_text),
        backup_text=backup_text,
        backup_sha256=backup_sha256,
        manifest=manifest,
        marker_error=marker_error,
        cron_current_text=cron_current_text,
        cron_current_sha256=cron_current_sha256,
        cron_backup_text=cron_backup_text,
        cron_backup_sha256=cron_backup_sha256,
        cron_marker_error=cron_marker_error,
    )


def _classify_evidence(
    detection: HermesDetection, evidence: RecoveryEvidence
) -> RecoveryClassification:
    parts = _fingerprint_parts(detection, evidence)
    read_findings = []
    if evidence.marker_error == "symlink_refused":
        read_findings.append(_finding("symlink_refused", "error"))
    elif evidence.marker_error == "current_read_error":
        read_findings.append(_finding("current_read_error", "error"))
    if evidence.cron_marker_error == "symlink_refused":
        read_findings.append(_finding("cron_symlink_refused", "error"))
    elif evidence.cron_marker_error == "current_read_error":
        read_findings.append(_finding("cron_current_read_error", "error"))
    if read_findings:
        return _classification("refused", False, (), read_findings, parts)

    gateway = _classify_gateway_evidence(detection, evidence)
    cron = _classify_cron_evidence(detection, evidence, gateway.state)
    return _merge_classifications(gateway, cron, parts)


def _classify_gateway_evidence(
    detection: HermesDetection, evidence: RecoveryEvidence
) -> RecoveryClassification:
    parts = _fingerprint_parts(detection, evidence)
    findings = []

    if evidence.marker_error == "symlink_refused":
        findings.append(_finding("symlink_refused", "error"))
        return _classification("refused", False, (), findings, parts)
    if evidence.marker_error == "current_read_error":
        findings.append(_finding("current_read_error", "error"))
        return _classification("refused", False, (), findings, parts)

    manifest = evidence.manifest
    manifest_present = manifest is not None
    manifest_invalid = bool(manifest and manifest.get(_MANIFEST_ERROR))
    manifest_usable = manifest_present and not manifest_invalid
    backup_present = evidence.backup_text is not None
    backup_status_error = evidence.backup_sha256.startswith(_STATUS_PREFIX)
    artifacts_present = manifest_present or backup_present or backup_status_error

    marker_corrupt = bool(evidence.marker_error)
    unpatched = evidence.current_text
    has_owned_patch = False
    if not marker_corrupt:
        try:
            unpatched = remove_patch(evidence.current_text)
            has_owned_patch = unpatched != evidence.current_text
        except ValueError:
            marker_corrupt = True

    if marker_corrupt:
        state = "corrupt_owned"
    elif has_owned_patch:
        state = "installed"
    elif artifacts_present:
        state = "stale_unpatched"
    else:
        state = "clean"

    if state == "clean":
        if not detection.supported and _is_anchor_refusal(detection.reason):
            findings.append(_finding("unsupported_anchors", "warning"))
        return _classification(state, False, (), findings, parts)

    manifest_checks = _check_manifest(detection, evidence, state)
    findings.extend(manifest_checks.findings)
    backup_checks = _check_backup(evidence)
    findings.extend(backup_checks.findings)

    if state == "stale_unpatched":
        actions = ("clear_stale_install_state",)
        current_valid = _is_valid_python(evidence.current_text)
        if not current_valid:
            findings.append(_finding("unsupported_anchors", "error"))

        if backup_present:
            source_matches = (
                backup_checks.valid
                and evidence.backup_text == evidence.current_text
            )
            if backup_checks.valid and not source_matches:
                findings.append(_finding("backup_source_mismatch", "error"))
        else:
            expected_backup = manifest_checks.backup_hash
            source_matches = bool(
                manifest_checks.valid
                and expected_backup
                and evidence.current_sha256 == expected_backup
            )
            if not backup_status_error:
                findings.append(_finding("backup_missing", "warning"))

        if manifest is None:
            findings.append(_finding("manifest_missing", "warning"))
            manifest_safe = backup_present and backup_checks.valid
        else:
            manifest_safe = manifest_checks.valid

        executable = bool(current_valid and source_matches and manifest_safe)
        return _classification(state, executable, actions, findings, parts)

    if marker_corrupt:
        findings.insert(0, _finding("marker_error", "error"))
        actions = ("restore_verified_backup", "reapply_current_hook")
        reapply = _validate_reapplication(detection, evidence.backup_text)
        if reapply == "unsupported_anchors":
            findings.append(_finding("unsupported_anchors", "error"))
        elif reapply:
            findings.append(_finding("reapplication_invalid", "error"))
        executable = bool(
            manifest_checks.valid
            and manifest_checks.current_matches
            and backup_checks.valid
            and manifest_checks.backup_matches
            and not reapply
        )
        return _classification(state, executable, actions, findings, parts)

    source_text = evidence.backup_text if backup_checks.valid else unpatched
    source_matches = not backup_present or evidence.backup_text == unpatched
    if backup_present and backup_checks.valid and not source_matches:
        findings.append(_finding("backup_source_mismatch", "error"))

    reapply_error = _validate_reapplication(detection, source_text)
    candidate_matches = False
    if not reapply_error and source_text is not None:
        candidate_matches = (
            apply_patch(source_text, strategy=detection.hook_strategy)
            == evidence.current_text
        )
    if reapply_error == "unsupported_anchors":
        findings.append(_finding("unsupported_anchors", "error"))
    elif reapply_error:
        findings.append(_finding("reapplication_invalid", "error"))
    elif not candidate_matches:
        findings.append(_finding("current_patch_mismatch", "error"))

    complete = bool(
        manifest_checks.valid
        and manifest_checks.current_matches
        and manifest_checks.backup_matches
        and backup_checks.valid
        and source_matches
        and candidate_matches
    )
    if complete:
        return _classification("installed", False, (), findings, parts)

    state = "owned_incomplete"
    actions = _incomplete_actions(
        manifest_present=manifest_present,
        manifest_valid=manifest_checks.valid,
        backup_present=backup_present,
        candidate_matches=candidate_matches,
    )
    if not backup_present and not backup_status_error:
        findings.append(_finding("backup_missing", "warning"))
    if manifest is None:
        findings.append(_finding("manifest_missing", "warning"))

    manifest_safe = manifest is None or manifest_checks.valid
    if manifest_usable and not manifest_checks.current_matches:
        manifest_safe = bool(
            manifest_checks.paths_valid
            and manifest_checks.backup_hash
            and manifest_checks.backup_matches
        )
    if not manifest_present and not backup_present:
        manifest_safe = True

    derived_backup_matches = bool(
        not manifest_usable
        or not manifest_checks.backup_hash
        or _text_sha256(unpatched) == manifest_checks.backup_hash
    )
    executable = bool(
        manifest_safe
        and not manifest_invalid
        and not backup_status_error
        and (not backup_present or backup_checks.valid)
        and source_matches
        and derived_backup_matches
        and candidate_matches
    )
    return _classification(state, executable, actions, findings, parts)


def plan_recovery(detection: HermesDetection) -> RecoveryPlan:
    evidence = _read_evidence(detection)
    classification = _classify_evidence(detection, evidence)
    return RecoveryPlan(
        root=detection.root,
        state=classification.state,
        executable=classification.executable,
        fingerprint=_fingerprint(classification.fingerprint_parts),
        actions=classification.actions,
        findings=classification.findings,
    )


def sanitize_recovery_plan(plan: RecoveryPlan) -> Dict[str, object]:
    return {
        "state": plan.state,
        "executable": plan.executable,
        "fingerprint": plan.fingerprint[:12],
        "actions": list(plan.actions),
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": _safe_message(finding.code),
            }
            for finding in plan.findings
        ],
    }


@dataclass(frozen=True)
class _ManifestChecks:
    valid: bool
    paths_valid: bool
    current_matches: bool
    backup_matches: bool
    backup_hash: str
    findings: Tuple[RecoveryFinding, ...]


@dataclass(frozen=True)
class _BackupChecks:
    valid: bool
    findings: Tuple[RecoveryFinding, ...]


def _classify_cron_evidence(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    gateway_state: str,
) -> RecoveryClassification:
    parts = _fingerprint_parts(detection, evidence)
    findings = []
    manifest = evidence.manifest
    manifest_invalid = bool(manifest and manifest.get(_MANIFEST_ERROR))
    manifest_has_cron = _manifest_has_cron_evidence(manifest)
    backup_present = evidence.cron_backup_text is not None
    backup_status_error = evidence.cron_backup_sha256.startswith(_STATUS_PREFIX)
    artifacts_present = manifest_has_cron or backup_present or backup_status_error

    if evidence.cron_current_text is None:
        if not artifacts_present:
            return _classification("clean", False, (), findings, parts)
        findings.append(_finding("cron_source_missing", "error"))
        findings.extend(_check_cron_backup(evidence).findings)
        findings.extend(_check_cron_manifest(detection, evidence, False).findings)
        return _classification("owned_incomplete", False, (), findings, parts)

    current = evidence.cron_current_text
    marker_corrupt = bool(evidence.cron_marker_error)
    unpatched = current
    has_owned_patch = False
    if not marker_corrupt:
        try:
            unpatched = remove_cron_patch(current)
            has_owned_patch = unpatched != current
        except ValueError:
            marker_corrupt = True

    manifest_checks = _check_cron_manifest(
        detection, evidence, marker_corrupt or has_owned_patch
    )
    backup_checks = _check_cron_backup(evidence)
    findings.extend(manifest_checks.findings)
    findings.extend(backup_checks.findings)

    if marker_corrupt:
        findings.insert(0, _finding("cron_marker_error", "error"))
        reapply_error = _validate_cron_reapplication(evidence.cron_backup_text)
        if reapply_error == "unsupported_anchors":
            findings.append(_finding("cron_unsupported_anchors", "error"))
        elif reapply_error:
            findings.append(_finding("cron_reapplication_invalid", "error"))
        executable = bool(
            manifest_has_cron
            and manifest_checks.valid
            and manifest_checks.current_matches
            and backup_checks.valid
            and manifest_checks.backup_matches
            and not reapply_error
        )
        return _classification(
            "corrupt_owned",
            executable,
            ("restore_verified_cron_backup", "reapply_current_cron_hook"),
            findings,
            parts,
        )

    if has_owned_patch:
        source_text = evidence.cron_backup_text if backup_checks.valid else unpatched
        source_matches = not backup_present or evidence.cron_backup_text == unpatched
        if backup_present and backup_checks.valid and not source_matches:
            findings.append(_finding("cron_backup_source_mismatch", "error"))

        reapply_error = _validate_cron_reapplication(source_text)
        candidate_matches = False
        if not reapply_error and source_text is not None:
            candidate_matches = apply_cron_patch(source_text) == current
        if reapply_error == "unsupported_anchors":
            findings.append(_finding("cron_unsupported_anchors", "error"))
        elif reapply_error:
            findings.append(_finding("cron_reapplication_invalid", "error"))
        elif not candidate_matches:
            findings.append(_finding("cron_current_patch_mismatch", "error"))

        complete = bool(
            manifest_has_cron
            and manifest_checks.valid
            and manifest_checks.current_matches
            and manifest_checks.backup_matches
            and backup_checks.valid
            and source_matches
            and candidate_matches
        )
        if complete:
            return _classification("installed", False, (), findings, parts)

        actions = []
        if not backup_present:
            actions.append("rebuild_cron_backup")
        if not manifest_has_cron or not manifest_checks.valid or not backup_present:
            actions.append("rebuild_manifest")
        if not candidate_matches:
            actions.append("reapply_current_cron_hook")

        derived_backup_matches = bool(
            not manifest_has_cron
            or not manifest_checks.backup_hash
            or _text_sha256(unpatched) == manifest_checks.backup_hash
        )
        manifest_safe = bool(
            not manifest_invalid
            and (
                not manifest_has_cron
                or (
                    manifest_checks.valid
                    and manifest_checks.current_matches
                    and (
                        not backup_present
                        or manifest_checks.backup_matches
                    )
                )
            )
        )
        executable = bool(
            manifest_safe
            and not backup_status_error
            and (not backup_present or backup_checks.valid)
            and source_matches
            and derived_backup_matches
            and candidate_matches
        )
        return _classification(
            "owned_incomplete",
            executable,
            tuple(actions or ["rebuild_manifest"]),
            findings,
            parts,
        )

    if artifacts_present:
        source_valid = _is_valid_python(current)
        source_matches = bool(
            (backup_present and backup_checks.valid and evidence.cron_backup_text == current)
            or (
                not backup_present
                and manifest_checks.backup_hash
                and evidence.cron_current_sha256 == manifest_checks.backup_hash
            )
        )
        if backup_present and backup_checks.valid and not source_matches:
            findings.append(_finding("cron_backup_source_mismatch", "error"))
        if not source_valid:
            findings.append(_finding("cron_unsupported_anchors", "error"))
        reapply_error = _validate_cron_reapplication(current)
        if reapply_error == "unsupported_anchors":
            findings.append(_finding("cron_unsupported_anchors", "error"))
        elif reapply_error:
            findings.append(_finding("cron_reapplication_invalid", "error"))
        executable = bool(
            source_valid
            and source_matches
            and manifest_has_cron
            and manifest_checks.valid
            and not manifest_invalid
            and not backup_status_error
            and not reapply_error
        )
        actions = (
            ("clear_stale_install_state",)
            if gateway_state in {"clean", "stale_unpatched"}
            else ("reapply_current_cron_hook", "rebuild_manifest")
        )
        return _classification(
            "stale_unpatched", executable, actions, findings, parts
        )

    if gateway_state == "clean":
        return _classification("clean", False, (), findings, parts)

    reapply_error = _validate_cron_reapplication(current)
    if reapply_error == "unsupported_anchors":
        findings.append(_finding("cron_unsupported_anchors", "error"))
    elif reapply_error:
        findings.append(_finding("cron_reapplication_invalid", "error"))
    findings.append(_finding("cron_manifest_missing", "warning"))
    return _classification(
        "owned_incomplete",
        not reapply_error and not manifest_invalid,
        (
            "rebuild_cron_backup",
            "reapply_current_cron_hook",
            "rebuild_manifest",
        ),
        findings,
        parts,
    )


def _merge_classifications(
    gateway: RecoveryClassification,
    cron: RecoveryClassification,
    parts: Dict[str, str],
) -> RecoveryClassification:
    states = {gateway.state, cron.state}
    if "refused" in states:
        state = "refused"
    elif "corrupt_owned" in states:
        state = "corrupt_owned"
    elif "owned_incomplete" in states:
        state = "owned_incomplete"
    elif "stale_unpatched" in states:
        state = "stale_unpatched"
    elif "installed" in states:
        state = "installed"
    else:
        state = "clean"

    actions, actions_safe = _merge_actions(gateway, cron)
    findings = gateway.findings + cron.findings
    healthy = {"clean", "installed"}
    executable = bool(
        state not in healthy
        and actions_safe
        and gateway.state != "refused"
        and cron.state != "refused"
        and (gateway.state in healthy or gateway.executable)
        and (cron.state in healthy or cron.executable)
    )
    return _classification(state, executable, actions, findings, parts)


def _merge_actions(
    gateway: RecoveryClassification,
    cron: RecoveryClassification,
) -> Tuple[Tuple[str, ...], bool]:
    clear_action = "clear_stale_install_state"
    if clear_action not in gateway.actions:
        return tuple(dict.fromkeys(gateway.actions + cron.actions)), True

    if cron.state in {"clean", "stale_unpatched"}:
        return (clear_action,), True
    if cron.state == "installed" or (
        cron.state == "corrupt_owned" and cron.executable
    ):
        return ("restore_verified_cron_backup", clear_action), True
    return (), False


def _check_manifest(
    detection: HermesDetection, evidence: RecoveryEvidence, state: str
) -> _ManifestChecks:
    manifest = evidence.manifest
    if manifest is None:
        return _ManifestChecks(False, False, False, False, "", ())
    if manifest.get(_MANIFEST_ERROR):
        return _ManifestChecks(
            False,
            False,
            False,
            False,
            "",
            (_finding("manifest_invalid", "error"),),
        )

    findings = []
    expected_run = _relative_path(detection.root, detection.run_py)
    backup_path = detection.run_py.with_name(
        f"{detection.run_py.name}{BACKUP_SUFFIX}"
    )
    expected_backup = _relative_path(detection.root, backup_path)
    paths_valid = bool(
        manifest.get("run_py") == expected_run
        and manifest.get("backup") == expected_backup
    )
    if not paths_valid:
        findings.append(_finding("manifest_path_mismatch", "error"))

    current_hash = _manifest_hash(manifest, "patched_sha256")
    backup_hash = _manifest_hash(manifest, "backup_sha256")
    if not current_hash:
        findings.append(_finding("manifest_current_hash_invalid", "error"))
    if not backup_hash:
        findings.append(_finding("manifest_backup_hash_invalid", "error"))

    current_matches = bool(current_hash and evidence.current_sha256 == current_hash)
    backup_matches = bool(
        backup_hash
        and evidence.backup_text is not None
        and evidence.backup_sha256 == backup_hash
    )
    if state != "stale_unpatched" and current_hash and not current_matches:
        findings.append(_finding("current_hash_mismatch", "error"))
    if evidence.backup_text is not None and backup_hash and not backup_matches:
        findings.append(_finding("backup_hash_mismatch", "error"))

    valid = bool(paths_valid and current_hash and backup_hash)
    return _ManifestChecks(
        valid,
        paths_valid,
        current_matches,
        backup_matches,
        backup_hash,
        tuple(findings),
    )


def _check_cron_manifest(
    detection: HermesDetection,
    evidence: RecoveryEvidence,
    require_current_hash_match: bool,
) -> _ManifestChecks:
    manifest = evidence.manifest
    if manifest is None or not _manifest_has_cron_evidence(manifest):
        return _ManifestChecks(False, False, False, False, "", ())
    if manifest.get(_MANIFEST_ERROR):
        return _ManifestChecks(False, False, False, False, "", ())

    findings = []
    cron_py = detection.cron_py
    if cron_py is None:
        paths_valid = False
    else:
        backup_path = cron_py.with_name(f"{cron_py.name}{BACKUP_SUFFIX}")
        paths_valid = bool(
            manifest.get("cron_py") == _relative_path(detection.root, cron_py)
            and manifest.get("cron_backup")
            == _relative_path(detection.root, backup_path)
        )
    if not paths_valid:
        findings.append(_finding("cron_manifest_path_mismatch", "error"))

    current_hash = _manifest_hash(manifest, "cron_patched_sha256")
    backup_hash = _manifest_hash(manifest, "cron_backup_sha256")
    if not current_hash:
        findings.append(_finding("cron_manifest_current_hash_invalid", "error"))
    if not backup_hash:
        findings.append(_finding("cron_manifest_backup_hash_invalid", "error"))

    current_matches = bool(
        current_hash and evidence.cron_current_sha256 == current_hash
    )
    backup_matches = bool(
        backup_hash
        and evidence.cron_backup_text is not None
        and evidence.cron_backup_sha256 == backup_hash
    )
    if require_current_hash_match and current_hash and not current_matches:
        findings.append(_finding("cron_current_hash_mismatch", "error"))
    if evidence.cron_backup_text is not None and backup_hash and not backup_matches:
        findings.append(_finding("cron_backup_hash_mismatch", "error"))

    valid = bool(paths_valid and current_hash and backup_hash)
    return _ManifestChecks(
        valid,
        paths_valid,
        current_matches,
        backup_matches,
        backup_hash,
        tuple(findings),
    )


def _check_backup(evidence: RecoveryEvidence) -> _BackupChecks:
    if evidence.backup_sha256 == f"{_STATUS_PREFIX}symlink":
        return _BackupChecks(False, (_finding("symlink_refused", "error"),))
    if evidence.backup_sha256 == f"{_STATUS_PREFIX}read_error":
        return _BackupChecks(False, (_finding("backup_read_error", "error"),))
    if evidence.backup_text is None:
        return _BackupChecks(False, ())
    try:
        ast.parse(evidence.backup_text)
        if remove_patch(evidence.backup_text) != evidence.backup_text:
            raise ValueError("owned patch in backup")
        if remove_cron_patch(evidence.backup_text) != evidence.backup_text:
            raise ValueError("owned cron patch in backup")
    except (SyntaxError, ValueError):
        return _BackupChecks(False, (_finding("backup_invalid", "error"),))
    return _BackupChecks(True, ())


def _check_cron_backup(evidence: RecoveryEvidence) -> _BackupChecks:
    if evidence.cron_backup_sha256 == f"{_STATUS_PREFIX}symlink":
        return _BackupChecks(False, (_finding("cron_symlink_refused", "error"),))
    if evidence.cron_backup_sha256 == f"{_STATUS_PREFIX}read_error":
        return _BackupChecks(
            False, (_finding("cron_backup_read_error", "error"),)
        )
    if evidence.cron_backup_text is None:
        return _BackupChecks(False, ())
    try:
        ast.parse(evidence.cron_backup_text)
        if remove_cron_patch(evidence.cron_backup_text) != evidence.cron_backup_text:
            raise ValueError("owned cron patch in backup")
    except (SyntaxError, ValueError):
        return _BackupChecks(False, (_finding("cron_backup_invalid", "error"),))
    return _BackupChecks(True, ())


def _validate_reapplication(
    detection: HermesDetection, source_text: Optional[str]
) -> str:
    if source_text is None or not detection.hook_strategy:
        return "unsupported_anchors"
    try:
        ast.parse(source_text)
        candidate = apply_patch(source_text, strategy=detection.hook_strategy)
        ast.parse(candidate)
        if remove_patch(candidate) != source_text:
            return "marker_validation"
    except (SyntaxError, ValueError):
        return "unsupported_anchors"
    return ""


def _validate_cron_reapplication(source_text: Optional[str]) -> str:
    if source_text is None:
        return "unsupported_anchors"
    try:
        ast.parse(source_text)
        candidate = apply_cron_patch(source_text)
        if candidate == source_text:
            return "unsupported_anchors"
        ast.parse(candidate)
        if remove_cron_patch(candidate) != source_text:
            return "marker_validation"
    except (SyntaxError, ValueError):
        return "unsupported_anchors"
    return ""


def _incomplete_actions(
    *,
    manifest_present: bool,
    manifest_valid: bool,
    backup_present: bool,
    candidate_matches: bool,
) -> Tuple[str, ...]:
    actions = []
    if not backup_present:
        actions.append("rebuild_backup")
    if not manifest_present or not manifest_valid or not backup_present:
        actions.append("rebuild_manifest")
    if not candidate_matches:
        actions.append("reapply_current_hook")
    if not actions:
        actions.append("rebuild_manifest")
    return tuple(actions)


def _classification(
    state: str,
    executable: bool,
    actions: Tuple[str, ...],
    findings,
    parts: Dict[str, str],
) -> RecoveryClassification:
    if state not in KNOWN_STATES:
        raise ValueError("unknown recovery state")
    return RecoveryClassification(
        state=state,
        executable=executable,
        fingerprint_parts=parts,
        actions=actions,
        findings=_deduplicate_findings(findings),
    )


def _fingerprint_parts(
    detection: HermesDetection, evidence: RecoveryEvidence
) -> Dict[str, str]:
    manifest = evidence.manifest
    if manifest is None:
        manifest_state = "missing"
        manifest_current_hash = ""
        manifest_backup_hash = ""
        manifest_cron_current_hash = ""
        manifest_cron_backup_hash = ""
        run_path_matches = "false"
        backup_path_matches = "false"
        cron_path_matches = "false"
        cron_backup_path_matches = "false"
    elif manifest.get(_MANIFEST_ERROR):
        manifest_state = str(manifest[_MANIFEST_ERROR])
        manifest_current_hash = ""
        manifest_backup_hash = ""
        manifest_cron_current_hash = ""
        manifest_cron_backup_hash = ""
        run_path_matches = "false"
        backup_path_matches = "false"
        cron_path_matches = "false"
        cron_backup_path_matches = "false"
    else:
        manifest_state = "present"
        manifest_current_hash = _manifest_hash(manifest, "patched_sha256")
        manifest_backup_hash = _manifest_hash(manifest, "backup_sha256")
        manifest_cron_current_hash = _manifest_hash(
            manifest, "cron_patched_sha256"
        )
        manifest_cron_backup_hash = _manifest_hash(
            manifest, "cron_backup_sha256"
        )
        expected_backup = detection.run_py.with_name(
            f"{detection.run_py.name}{BACKUP_SUFFIX}"
        )
        run_path_matches = str(
            manifest.get("run_py") == _relative_path(detection.root, detection.run_py)
        ).lower()
        backup_path_matches = str(
            manifest.get("backup") == _relative_path(detection.root, expected_backup)
        ).lower()
        cron_py = detection.cron_py
        if cron_py is None:
            cron_path_matches = "false"
            cron_backup_path_matches = "false"
        else:
            expected_cron_backup = cron_py.with_name(
                f"{cron_py.name}{BACKUP_SUFFIX}"
            )
            cron_path_matches = str(
                manifest.get("cron_py")
                == _relative_path(detection.root, cron_py)
            ).lower()
            cron_backup_path_matches = str(
                manifest.get("cron_backup")
                == _relative_path(detection.root, expected_cron_backup)
            ).lower()

    return {
        "backup_path_matches": backup_path_matches,
        "backup_sha256": evidence.backup_sha256,
        "cron_backup_path_matches": cron_backup_path_matches,
        "cron_backup_sha256": evidence.cron_backup_sha256,
        "cron_current_sha256": evidence.cron_current_sha256,
        "cron_marker_error": evidence.cron_marker_error,
        "cron_path_matches": cron_path_matches,
        "current_sha256": evidence.current_sha256,
        "hook_strategy": detection.hook_strategy,
        "manifest_backup_sha256": manifest_backup_hash,
        "manifest_cron_backup_sha256": manifest_cron_backup_hash,
        "manifest_cron_current_sha256": manifest_cron_current_hash,
        "manifest_current_sha256": manifest_current_hash,
        "manifest_state": manifest_state,
        "marker_error": evidence.marker_error,
        "run_path_matches": run_path_matches,
        "supported": str(detection.supported).lower(),
    }


def _read_cron_evidence(
    detection: HermesDetection,
) -> Tuple[Optional[str], str, Optional[str], str, str]:
    cron_py = detection.cron_py
    if cron_py is None:
        return None, "", None, "", ""

    backup_path = cron_py.with_name(f"{cron_py.name}{BACKUP_SUFFIX}")
    backup_text: Optional[str] = None
    backup_sha256 = ""
    if backup_path.is_symlink():
        backup_sha256 = f"{_STATUS_PREFIX}symlink"
    elif backup_path.exists():
        try:
            backup_text = _read_text(backup_path)
            backup_sha256 = _text_sha256(backup_text)
        except (OSError, UnicodeError):
            backup_sha256 = f"{_STATUS_PREFIX}read_error"

    if cron_py.is_symlink():
        return None, "", backup_text, backup_sha256, "symlink_refused"
    if not cron_py.exists():
        return None, "", backup_text, backup_sha256, ""
    try:
        current_text = _read_text(cron_py)
    except (OSError, UnicodeError):
        return None, "", backup_text, backup_sha256, "current_read_error"

    marker_error = ""
    try:
        remove_cron_patch(current_text)
    except ValueError:
        marker_error = "corrupt_patch_markers"
    return (
        current_text,
        _text_sha256(current_text),
        backup_text,
        backup_sha256,
        marker_error,
    )


def _manifest_has_cron_evidence(
    manifest: Optional[Dict[str, object]],
) -> bool:
    if manifest is None or manifest.get(_MANIFEST_ERROR):
        return False
    return any(
        key in manifest
        for key in (
            "cron_py",
            "cron_patched_sha256",
            "cron_backup",
            "cron_backup_sha256",
        )
    )


def _read_manifest_evidence(path: Path) -> Optional[Dict[str, object]]:
    if path.is_symlink():
        return {_MANIFEST_ERROR: "symlink"}
    if not path.exists():
        return None
    try:
        value = json.loads(_read_text(path))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {_MANIFEST_ERROR: "invalid"}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return {_MANIFEST_ERROR: "invalid"}
    return value


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _manifest_hash(manifest: Dict[str, object], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str):
        return ""
    normalized = value.lower()
    return normalized if _HASH_RE.fullmatch(normalized) else ""


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return ""


def _is_valid_python(value: str) -> bool:
    try:
        ast.parse(value)
    except SyntaxError:
        return False
    return True


def _is_anchor_refusal(reason: str) -> bool:
    lowered = reason.lower()
    return any(
        marker in lowered
        for marker in ("anchor", "parse", "handler", "unsupported")
    )


def _finding(code: str, severity: str) -> RecoveryFinding:
    return RecoveryFinding(code, severity, _safe_message(code))


def _safe_message(code: str) -> str:
    messages = {
        "backup_hash_mismatch": "Backup evidence does not match the install manifest.",
        "backup_invalid": "The backup is not valid unpatched source.",
        "backup_missing": "The owned hook backup is missing.",
        "backup_read_error": "The owned hook backup could not be read.",
        "backup_source_mismatch": "Backup source does not match the owned hook source.",
        "current_hash_mismatch": "Current hook evidence does not match the install manifest.",
        "current_patch_mismatch": "The current owned hook cannot be reproduced safely.",
        "current_read_error": "Current hook source could not be read.",
        "cron_backup_hash_mismatch": "Cron backup evidence does not match the install manifest.",
        "cron_backup_invalid": "The cron backup is not valid unpatched source.",
        "cron_backup_read_error": "The cron backup could not be read.",
        "cron_backup_source_mismatch": "Cron backup source does not match the owned hook source.",
        "cron_current_hash_mismatch": "Current cron evidence does not match the install manifest.",
        "cron_current_patch_mismatch": "The current cron hook cannot be reproduced safely.",
        "cron_current_read_error": "Current cron source could not be read.",
        "cron_manifest_backup_hash_invalid": "The manifest cron backup fingerprint is missing or invalid.",
        "cron_manifest_current_hash_invalid": "The manifest cron fingerprint is missing or invalid.",
        "cron_manifest_missing": "The owned cron manifest evidence is missing.",
        "cron_manifest_path_mismatch": "Cron manifest ownership paths do not match the detected install.",
        "cron_marker_error": "Owned cron hook markers are incomplete or invalid.",
        "cron_reapplication_invalid": "The current cron hook cannot be validated in memory.",
        "cron_source_missing": "The owned cron source is missing.",
        "cron_symlink_refused": "Recovery does not operate on cron symbolic links.",
        "cron_unsupported_anchors": "Verified cron source does not support the current hook strategy.",
        "manifest_backup_hash_invalid": "The manifest backup fingerprint is missing or invalid.",
        "manifest_current_hash_invalid": "The manifest current fingerprint is missing or invalid.",
        "manifest_invalid": "The install manifest is invalid.",
        "manifest_missing": "The owned hook manifest is missing.",
        "manifest_path_mismatch": "Manifest ownership paths do not match the detected install.",
        "marker_error": "Owned hook markers are incomplete or invalid.",
        "reapplication_invalid": "The current hook cannot be validated in memory.",
        "symlink_refused": "Recovery does not operate on symbolic links.",
        "unsupported_anchors": "Verified source does not support the current hook strategy.",
    }
    return messages.get(code, "Recovery evidence requires review.")


def _deduplicate_findings(findings) -> Tuple[RecoveryFinding, ...]:
    result = []
    seen = set()
    for finding in findings:
        key = (finding.code, finding.severity)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return tuple(result)
