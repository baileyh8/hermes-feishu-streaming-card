"""External immutable wheel publication boundaries, without a PM runtime."""
from hashlib import sha256
import os
from pathlib import Path

import pytest

from hermes_feishu_card.install import pm_wheel_store as store
from hermes_feishu_card.install import posix_transaction as io

_REAL_DIRFD = io.DIRFD_BINDING_SUPPORTED

NAME = 'hermes_feishu_streaming_card-1-py3-none-any.whl'


@pytest.mark.parametrize('dirfd', [True, False])
def test_immutable_publication_and_reuse(tmp_path, monkeypatch, dirfd):
    if dirfd and not _REAL_DIRFD:
        pytest.skip('directory descriptor operations unavailable')
    monkeypatch.setattr(io, 'DIRFD_BINDING_SUPPORTED', dirfd)
    reference = store.publish(tmp_path, NAME, b'first')
    second = store.publish(tmp_path, NAME, b'second')
    assert reference != second
    assert Path(reference['path']).read_bytes() == b'first'
    assert store.publish(tmp_path, NAME, b'first') == reference
    assert not list(tmp_path.rglob('.hfc-wheel-*'))


@pytest.mark.parametrize('dirfd', [True, False])
def test_exclusive_publish_destination_race_keeps_unowned_bytes(tmp_path, monkeypatch, dirfd):
    if dirfd and not _REAL_DIRFD:
        pytest.skip('directory descriptor operations unavailable')
    monkeypatch.setattr(io, 'DIRFD_BINDING_SUPPORTED', dirfd)
    link = os.link
    def replace_destination(source, target, **kwargs):
        if 'dst_dir_fd' in kwargs:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
                                 dir_fd=kwargs['dst_dir_fd'])
            os.write(descriptor, b'unowned')
            os.close(descriptor)
        else:
            Path(target).write_bytes(b'unowned')
        return link(source, target, **kwargs)
    monkeypatch.setattr(store.os, 'link', replace_destination)
    with pytest.raises(ValueError):
        store.publish(tmp_path, NAME, b'owned')
    target = tmp_path / '.hermes_feishu_card/wheel-store' / sha256(b'owned').hexdigest() / NAME
    assert target.read_bytes() == b'unowned'


@pytest.mark.parametrize('dirfd', [True, False])
def test_fsync_failure_never_publishes_partial_final(tmp_path, monkeypatch, dirfd):
    if dirfd and not _REAL_DIRFD:
        pytest.skip('directory descriptor operations unavailable')
    monkeypatch.setattr(io, 'DIRFD_BINDING_SUPPORTED', dirfd)
    def fail(descriptor):
        raise OSError('injected durability failure')
    monkeypatch.setattr(store.os, 'fsync', fail)
    with pytest.raises(ValueError):
        store.publish(tmp_path, NAME, b'owned')
    assert not list(tmp_path.rglob('*.whl'))
    assert not list(tmp_path.rglob('.hfc-wheel-*'))


def test_posix_foreign_owner_directory_is_rejected(tmp_path, monkeypatch):
    if not store._POSIX_PERMISSIONS or not hasattr(os, 'getuid'):
        pytest.skip('POSIX UID ownership boundary')
    (tmp_path / '.hermes_feishu_card').mkdir(mode=0o700)
    actual = os.getuid()
    monkeypatch.setattr(store.os, 'getuid', lambda: actual + 1)
    with pytest.raises(ValueError):
        store.publish(tmp_path, NAME, b'owned')


@pytest.mark.parametrize('filename', ['../bad.whl', 'bad.whl', 'hermes_feishu_streaming_card-1.whl'])
def test_invalid_wheel_filename_refuses_without_filesystem_changes(tmp_path, filename):
    with pytest.raises(ValueError):
        store.publish(tmp_path, filename, b'owned')
    assert not list(tmp_path.iterdir())


def test_windows_permission_branch_accepts_inherited_acl_mode_bits(tmp_path, monkeypatch):
    # Windows stat commonly reports directory 0777/file 0666 regardless of
    # requested Unix mode. Exercise the platform policy without claiming ACL QA.
    monkeypatch.setattr(store, '_POSIX_PERMISSIONS', False)
    monkeypatch.setattr(io, 'DIRFD_BINDING_SUPPORTED', False)
    reference = store.publish(tmp_path, NAME, b'windows-policy')
    path = Path(reference['path'])
    path.chmod(0o666)
    for directory in [path.parent, path.parent.parent, path.parent.parent.parent]:
        directory.chmod(0o777)
    assert store.validate(tmp_path, reference) == path


def test_posix_permission_branch_rejects_public_wheel(tmp_path):
    if not store._POSIX_PERMISSIONS:
        pytest.skip('POSIX permission boundary')
    reference = store.publish(tmp_path, NAME, b'private')
    Path(reference['path']).chmod(0o644)
    with pytest.raises(ValueError):
        store.validate(tmp_path, reference)
