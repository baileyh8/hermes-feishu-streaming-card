"""Real directory-bound I/O regressions for three independently gated writers."""
from __future__ import annotations

import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_feishu_card.install import (
    file_transaction as portable,
    integrity,
    posix_transaction as io,
    recovery,
)


def test_platform_binary_flag_is_zero_on_posix():
    if os.name != "nt":
        assert portable.BINARY_FLAG == io.BINARY_FLAG == 0


def test_byte_reads_and_stages_request_platform_binary_mode(tmp_path, monkeypatch):
    target = tmp_path / "target.bin"
    target.write_bytes(b"before\r\n\x1aafter")
    injected = 0x40000000
    seen = []
    real_open = io.os.open

    def opened(path, flags, *args, **kwargs):
        seen.append(flags)
        return real_open(path, flags & ~injected, *args, **kwargs)

    monkeypatch.setattr(io, "BINARY_FLAG", injected)
    monkeypatch.setattr(portable, "BINARY_FLAG", injected)
    monkeypatch.setattr(io.os, "open", opened)

    identity = io.read_path_snapshot(target, ValueError, "read refused")
    assert portable._read_restore_text(target, identity) == "before\r\n\x1aafter"
    descriptor = portable._open_portable_exclusive_fd(target, create=False)
    os.close(descriptor)
    staged = io.stage_file(
        target,
        b"new\r\n\x1abytes",
        binding=None,
        mode=0o600,
        durable_text=False,
        refused=ValueError,
        staging_refusal="stage refused",
        target_refusal="target refused",
        stage_refusal="stage refused",
    )
    try:
        assert staged.path.read_bytes() == b"new\r\n\x1abytes"
    finally:
        staged.cleanup()
    assert sum(bool(flags & injected) for flags in seen) >= 4


@pytest.mark.parametrize("failure", ["last-mark", "last-close"])
def test_portable_family_evidence_failure_restores_the_whole_set(tmp_path, monkeypatch, failure):
    root = tmp_path / "family"
    root.mkdir()
    values = {
        root / "run.py.hermes_feishu_card.bak": "run\r\n",
        root / "cron.py.hermes_feishu_card.bak": "cron\r\n",
        root / ".hermes_feishu_card_manifest": '{"manifest": true}\r\n',
    }
    for path, value in values.items():
        path.write_text(value, encoding="utf-8")
    identities, directories = portable._snapshot_restore_evidence(
        root, {path.name: path for path in values}
    )
    paths = list(values)
    opened: dict[int, Path] = {}
    marked: set[int] = set()
    real_open, real_close = portable.os.open, portable.os.close
    marks = 0
    closes = 0
    failed = False
    evidence_checks = []

    def open_delete(path):
        descriptor = real_open(path, os.O_RDONLY)
        opened[descriptor] = Path(path)
        return descriptor

    def set_delete(descriptor, enabled):
        nonlocal marks, failed
        if enabled:
            marks += 1
            if failure == "last-mark" and marks == len(paths) and not failed:
                failed = True
                raise OSError("injected last mark failure")
            marked.add(descriptor)
        else:
            marked.discard(descriptor)

    def close(descriptor):
        nonlocal closes, failed
        closes += 1
        if failure == "last-close" and closes == len(paths) and not failed:
            failed = True
            raise OSError("injected last close failure")
        path = opened.pop(descriptor, None)
        deleting = descriptor in marked
        marked.discard(descriptor)
        real_close(descriptor)
        if deleting and path is not None:
            path.unlink()

    def open_exclusive(path, *, create):
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        return real_open(path, flags, 0o600)

    monkeypatch.setattr(portable.os, "name", "nt")
    original_check = portable._assert_restore_evidence_set_unchanged
    monkeypatch.setattr(
        portable,
        "_assert_restore_evidence_set_unchanged",
        lambda selected: evidence_checks.append(len(opened)) or original_check(selected),
    )
    monkeypatch.setattr(portable, "_open_portable_delete_fd", open_delete)
    monkeypatch.setattr(portable, "_set_portable_delete", set_delete)
    monkeypatch.setattr(portable, "_open_portable_exclusive_fd", open_exclusive)
    monkeypatch.setattr(portable.os, "close", close)

    with pytest.raises((OSError, ValueError)):
        portable._remove_targets_portably(
            paths,
            expected_identities=identities,
            expected_directories=directories,
            recovery_contents=values,
        )
    assert {path: path.read_bytes() for path in paths} == {
        path: value.encode("utf-8") for path, value in values.items()
    }
    assert evidence_checks == [0]


def test_portable_evidence_recreation_refuses_replaced_directory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    evidence = root / "run.py.hermes_feishu_card.bak"
    evidence.write_text("original\r\n", encoding="utf-8")
    identities, directories = portable._snapshot_restore_evidence(
        root, {"backup": evidence}
    )
    moved = tmp_path / "root-moved"
    root.rename(moved)
    root.mkdir()
    replacement = root / evidence.name

    error = portable._restore_portable_evidence(
        [replacement],
        {replacement: identities[evidence]},
        {replacement: "original\r\n"},
        directories,
    )

    assert error is not None
    assert not list(root.iterdir())


@pytest.mark.skipif(not io.DIRFD_BINDING_SUPPORTED, reason="requires secure POSIX directory-relative I/O")
@pytest.mark.parametrize("entry", ["recovery", "integrity", "cli"])
def test_cleanup_first_error_releases_every_acquired_stage_and_binding(tmp_path, monkeypatch, entry):
    targets = [tmp_path / name for name in ("first.txt", "second.txt")]
    for target in targets:
        target.write_bytes(b"original\r\n")
    stages, bindings, fds = [], [], []
    original_stage, original_bind, original_stat = io.stage_file, io.bind_target, io.os.stat
    failure = RuntimeError("first cleanup observation failed")
    triggered = []

    def stage(*args, **kwargs):
        value = original_stage(*args, **kwargs)
        stages.append(value)
        if value.owns_parent_fd:
            fds.append(value.parent_fd)
        return value

    def bind(*args, **kwargs):
        value = original_bind(*args, **kwargs)
        bindings.append(value)
        fds.append(value.parent_fd)
        return value

    # CLI constructs its derived binding after the common physical parent open.
    original_open = io.open_bound_parent
    def open_parent(*args, **kwargs):
        value = original_open(*args, **kwargs)
        if entry == "cli":
            fds.append(value)
        return value

    def stat(path, *args, **kwargs):
        # The first rollback stage remains unconsumed until final cleanup. Do
        # not interrupt staging, commit validation or the target itself.
        if stages and path == stages[0].basename and not triggered:
            triggered.append(path)
            raise failure
        return original_stat(path, *args, **kwargs)

    plan = SimpleNamespace(fingerprint="stable")
    monkeypatch.setattr(recovery, "plan_recovery", lambda *_args, **_kwargs: plan)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(io, "stage_file", stage)
            patch.setattr(io, "bind_target", bind)
            patch.setattr(io, "open_bound_parent", open_parent)
            patch.setattr(io.os, "stat", stat)
            with pytest.raises(RuntimeError) as caught:
                if entry == "recovery":
                    recovery._commit_recovery_changes(SimpleNamespace(root=tmp_path), plan,
                                                      [(p, "new\r\n") for p in targets])
                elif entry == "integrity":
                    integrity._atomic_replace_many([(p, "new\r\n") for p in targets], controlled_root=tmp_path)
                else:
                    io._atomic_write_text(targets[0], "new\r\n")
            assert caught.value is failure
        assert triggered
        for fd in fds:
            with pytest.raises(OSError) as closed:
                os.fstat(fd)
            assert closed.value.errno == errno.EBADF
        assert all(p.read_bytes() == b"new\r\n" for p in targets[:1 if entry == "cli" else 2])
    finally:
        # Regression failures retain logs/scratch, but never leave leaked test
        # descriptors in the pytest process or delete an unverified staged inode.
        for fd in set(fds):
            try:
                os.close(fd)
            except OSError:
                pass


def write(entry, target, monkeypatch, text="new\r\n"):
    if entry == "recovery":
        plan = SimpleNamespace(fingerprint="stable")
        monkeypatch.setattr(recovery, "plan_recovery", lambda *_args, **_kwargs: plan)
        return recovery._commit_recovery_changes(SimpleNamespace(root=target.parent), plan, [(target, text)])
    if entry == "integrity":
        return integrity._atomic_replace_many([(target, text)], controlled_root=target.parent)
    return io._atomic_write_text(target, text)


@pytest.mark.skipif(not io.DIRFD_BINDING_SUPPORTED, reason="requires secure POSIX directory-relative I/O")
@pytest.mark.parametrize("entry", ["recovery", "integrity", "cli"])
def test_three_writers_use_shared_owner_and_preserve_durability_fd_policy(tmp_path, monkeypatch, entry):
    target = tmp_path / "target.txt"
    target.write_bytes(b"old\r\n")
    target.chmod(0o640)
    seen, stages = [], []
    original_stage, original_replace = io.stage_file, io.replace_stage
    original_sync, original_chmod = io.os.fsync, io.os.fchmod
    original_fdopen = io.os.fdopen

    def stage(path, content, **kwargs):
        seen.append(("stage", kwargs["durable_text"], kwargs["binding"].parent_fd))
        result = original_stage(path, content, **kwargs)
        stages.append(result)
        assert result.owns_parent_fd is (entry != "cli")
        if entry == "cli":
            assert result.parent_fd == kwargs["binding"].parent_fd
        else:
            assert result.parent_fd != kwargs["binding"].parent_fd
        return result

    def replace(binding, staged, **kwargs):
        seen.append(("replace", staged.basename))
        return original_replace(binding, staged, **kwargs)

    def sync(fd):
        seen.append(("fsync", fd))
        return original_sync(fd)

    def chmod(fd, mode):
        seen.append(("fchmod", fd))
        return original_chmod(fd, mode)

    class TracedText:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def write(self, text):
            seen.append(("write", self.handle.fileno()))
            return self.handle.write(text)
        def flush(self):
            seen.append(("flush", self.handle.fileno()))
            return self.handle.flush()
        def fileno(self):
            return self.handle.fileno()

    monkeypatch.setattr(io.os, "fdopen", lambda *a, **kw: TracedText(original_fdopen(*a, **kw)))
    monkeypatch.setattr(io, "stage_file", stage)
    monkeypatch.setattr(io, "replace_stage", replace)
    monkeypatch.setattr(io.os, "fsync", sync)
    monkeypatch.setattr(io.os, "fchmod", chmod)
    write(entry, target, monkeypatch)
    assert target.read_bytes() == b"new\r\n"
    assert target.stat().st_mode & 0o777 == 0o640
    assert len(stages) == 2  # original rollback + proposed content
    assert len([row for row in seen if row[0] == "replace"]) == 1
    operations = [row[0] for row in seen]
    assert operations.count("fsync") == (0 if entry == "cli" else 2)
    assert operations.count("fchmod") == 2
    if entry != "cli":
        assert operations == ["stage", "write", "flush", "fsync", "fchmod",
                              "stage", "write", "flush", "fsync", "fchmod", "replace"]


@pytest.mark.skipif(not io.DIRFD_BINDING_SUPPORTED, reason="requires secure POSIX directory-relative I/O")
@pytest.mark.parametrize("entry", ["recovery", "integrity", "cli"])
@pytest.mark.parametrize("fault", ["bind", "read", "write", "fchmod", "replace"])
def test_physical_fault_reaches_shared_owner_and_leaves_original_without_fd_leak(tmp_path, monkeypatch, entry, fault):
    target = tmp_path / "target.txt"
    target.write_bytes(b"old\r\n")
    fds, reached = [], []
    original_open, original_dup = io.os.open, io.os.dup
    original_read, original_chmod, original_replace = io.os.read, io.os.fchmod, io.os.replace
    original_write, original_fdopen = io.os.write, io.os.fdopen

    def fail():
        reached.append(fault)
        raise OSError(errno.EIO, "injected physical " + fault)

    def opened(path, flags, *args, **kwargs):
        if fault == "bind" and os.fspath(path) == os.fspath(tmp_path):
            fail()
        fd = original_open(path, flags, *args, **kwargs)
        fds.append(fd)
        return fd

    def duplicated(fd):
        result = original_dup(fd)
        fds.append(result)
        return result

    class FailingText:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def write(self, content):
            fail()

    def fdopen(fd, *args, **kwargs):
        handle = original_fdopen(fd, *args, **kwargs)
        return FailingText(handle) if fault == "write" else handle

    def read(fd, count):
        if fault == "read":
            fail()
        return original_read(fd, count)

    def chmod(fd, mode):
        if fault == "fchmod":
            fail()
        return original_chmod(fd, mode)

    def replace(*args, **kwargs):
        if fault == "replace":
            fail()
        return original_replace(*args, **kwargs)

    def oswrite(fd, content):
        if fault == "write":
            fail()
        return original_write(fd, content)

    with monkeypatch.context() as patch:
        for name, action in (("open", opened), ("dup", duplicated), ("read", read),
                             ("fchmod", chmod), ("replace", replace),
                             ("write", oswrite), ("fdopen", fdopen)):
            patch.setattr(io.os, name, action)
        with pytest.raises((OSError, ValueError)):
            write(entry, target, monkeypatch)
    assert reached == [fault]
    assert target.read_bytes() == b"old\r\n"
    assert not list(tmp_path.glob(".*.tmp"))
    for fd in set(fds):
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == errno.EBADF


@pytest.mark.skipif(not io.DIRFD_BINDING_SUPPORTED, reason="requires secure POSIX directory-relative I/O")
@pytest.mark.parametrize("entry", ["recovery", "integrity"])
def test_durable_stage_fsync_failure_is_not_silently_downgraded(tmp_path, monkeypatch, entry):
    target = tmp_path / "target.txt"
    target.write_bytes(b"old\n")
    reached = []
    def fail(fd):
        reached.append(fd)
        raise OSError(errno.EIO, "durability refused")
    monkeypatch.setattr(io.os, "fsync", fail)
    with pytest.raises((OSError, ValueError)) as caught:
        write(entry, target, monkeypatch)
    cause = caught.value if isinstance(caught.value, OSError) else caught.value.__cause__
    assert isinstance(cause, OSError) and cause.errno == errno.EIO
    assert len(reached) == 1
    assert target.read_bytes() == b"old\n"
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.skipif(not io.DIRFD_BINDING_SUPPORTED, reason="requires secure POSIX directory-relative I/O")
@pytest.mark.parametrize("entry", ["recovery", "integrity", "cli"])
@pytest.mark.parametrize("replacement", ["different_inode", "symlink"])
def test_changed_stage_is_rejected_and_unknown_replacement_is_preserved(tmp_path, monkeypatch, entry, replacement):
    target = tmp_path / "target.txt"
    target.write_bytes(b"old\n")
    external = tmp_path / "external.txt"
    external.write_bytes(b"USER\n")
    original_replace = io.replace_stage
    changed = []
    def replace(binding, staged, **kwargs):
        path = staged.path
        decoy = tmp_path / "decoy.txt"
        if replacement == "different_inode":
            decoy.write_bytes(b"UNKNOWN\n")
            assert decoy.stat().st_ino != staged.identity[1]
        path.unlink()
        if replacement == "symlink":
            path.symlink_to(external)
        else:
            # A pre-existing distinct inode avoids filesystem inode reuse.
            decoy.replace(path)
        changed.append(path)
        return original_replace(binding, staged, **kwargs)
    monkeypatch.setattr(io, "replace_stage", replace)
    with pytest.raises(ValueError, match="staged file"):
        write(entry, target, monkeypatch)
    assert len(changed) == 1
    assert changed[0].exists()
    assert changed[0].is_symlink() is (replacement == "symlink")
    assert changed[0].read_bytes() == (b"USER\n" if replacement == "symlink" else b"UNKNOWN\n")
    assert target.read_bytes() == b"old\n"
    assert external.read_bytes() == b"USER\n"


@pytest.mark.skipif(not io.DIRFD_BINDING_SUPPORTED, reason="requires secure POSIX directory-relative I/O")
@pytest.mark.parametrize("entry", ["recovery", "integrity", "cli"])
def test_stage_same_inode_edit_is_never_reported_as_verified_commit(tmp_path, monkeypatch, entry):
    target = tmp_path / "target.txt"
    target.write_bytes(b"old\n")
    original_replace = io.replace_stage
    reached = []
    def replace(binding, staged, **kwargs):
        inode = staged.path.stat().st_ino
        staged.path.write_bytes(b"UNPLANNED\n")
        assert staged.path.stat().st_ino == inode
        reached.append(staged.basename)
        return original_replace(binding, staged, **kwargs)
    monkeypatch.setattr(io, "replace_stage", replace)
    with pytest.raises(ValueError):
        write(entry, target, monkeypatch)
    assert reached
    # The original protocols verify the committed digest after replacement and
    # refuse uncertain rollback ownership. Do not invent pre-write digest gates
    # or claim that this user-modified file was successfully restored.
    assert target.read_bytes() == b"UNPLANNED\n"


@pytest.mark.skipif(not io.DIRFD_BINDING_SUPPORTED, reason="requires secure POSIX directory-relative I/O")
@pytest.mark.parametrize("entry", ["recovery", "integrity", "cli"])
def test_bound_unlink_failure_is_observable_and_not_a_false_restore(tmp_path, monkeypatch, entry):
    target = tmp_path / "target.txt"
    reached = []
    original_unlink = io.os.unlink
    def unlink(path, *args, **kwargs):
        if path == target.name and kwargs.get("dir_fd") is not None:
            reached.append(path)
            raise OSError(errno.EIO, "owned target unlink refused")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(io.os, "unlink", unlink)
    if entry == "recovery":
        target.write_text("old\n")
        plan = SimpleNamespace(fingerprint="stable")
        monkeypatch.setattr(recovery, "plan_recovery", lambda *_args, **_kwargs: plan)
        with pytest.raises(OSError, match="unlink refused"):
            recovery._commit_recovery_changes(SimpleNamespace(root=tmp_path), plan, [(target, None)])
        assert target.read_text() == "old\n"
    else:
        def reject():
            raise ValueError("post-commit rejected")
        with pytest.raises(ValueError, match="manual review|rollback failed"):
            if entry == "integrity":
                integrity._atomic_replace_many([(target, "new\n")], controlled_root=tmp_path, validate=reject)
            else:
                io._write_targets_transactionally([(target, "new\n")], validate=reject)
        assert target.read_text() == "new\n"
    assert reached == [target.name]


@pytest.mark.skipif(not io.DIRFD_BINDING_SUPPORTED, reason="requires secure POSIX directory-relative I/O")
@pytest.mark.parametrize("entry", ["recovery", "integrity", "cli"])
def test_uninspectable_target_is_distinct_from_absence(tmp_path, monkeypatch, entry):
    target = tmp_path / "target.txt"
    target.write_bytes(b"owned\n")
    original_stat = io.os.stat
    reached = []
    def stat(path, *args, **kwargs):
        if os.fspath(path) in {os.fspath(target), target.name}:
            reached.append(path)
            raise PermissionError("target metadata unavailable")
        return original_stat(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(io.os, "stat", stat)
        with pytest.raises((OSError, ValueError)):
            write(entry, target, monkeypatch)
    assert reached
    assert target.read_bytes() == b"owned\n"
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.skipif(not io.DIRFD_BINDING_SUPPORTED, reason="requires secure POSIX directory-relative I/O")
def test_cleanup_unknown_stage_preserves_inode_and_releases_all_descriptors(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    bindings = [io.bind_target(p, ValueError, staging_refusal="stage", parent_refusal="parent", wrap_open_error=True)
                for p in (first, second)]
    stages = [io.stage_file(p, "new\n", binding=b, mode=None, durable_text=True,
                            refused=ValueError, staging_refusal="stage", target_refusal="target", stage_refusal="owner")
              for p, b in zip((first, second), bindings)]
    fds = [resource.parent_fd for resource in (*stages, *bindings)]
    unknown = tmp_path / "unknown.txt"
    unknown.write_text("user-owned\n")
    replacement = stages[0].path
    unknown.replace(replacement)
    io.cleanup_transaction_resources(stages, bindings)
    assert replacement.read_text() == "user-owned\n"
    assert not stages[1].path.exists()
    for fd in fds:
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == errno.EBADF


def test_cleanup_propagates_first_error_object_after_attempting_all_resources():
    trace = []
    first, second = OSError("first"), ValueError("second")
    def fail(label, error):
        def call():
            trace.append(label)
            raise error
        return call
    stages = [SimpleNamespace(cleanup=fail("stage1", first)),
              SimpleNamespace(cleanup=fail("stage2", second))]
    bindings = [SimpleNamespace(release=lambda: trace.append("binding1")),
                SimpleNamespace(release=lambda: trace.append("binding2"))]
    with pytest.raises(OSError) as caught:
        io.cleanup_transaction_resources(stages, bindings)
    assert caught.value is first
    assert trace == ["stage1", "stage2", "binding1", "binding2"]
