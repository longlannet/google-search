import fcntl
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_transaction_module():
    path = ROOT / 'scripts' / 'venv_transaction.py'
    spec = importlib.util.spec_from_file_location('google_search_venv_transaction', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_lock(path):
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return descriptor


def test_cli_uses_sys_argv_without_a_traceback(tmp_path):
    script = ROOT / 'scripts' / 'venv_transaction.py'

    missing = subprocess.run(
        [sys.executable, '-I', '-B', script],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert missing.returncode == 2
    assert missing.stdout == ''
    assert missing.stderr == ''

    lock_path = tmp_path / '.venv.install.lock'
    lock_fd = create_lock(lock_path)
    try:
        inspected = subprocess.run(
            [
                sys.executable,
                '-I',
                '-B',
                script,
                'inspect',
                tmp_path,
                str(lock_fd),
                lock_path,
                str(os.geteuid()),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            pass_fds=(lock_fd,),
        )
    finally:
        os.close(lock_fd)
    assert inspected.returncode == 0
    assert inspected.stdout == 'absent\n'
    assert inspected.stderr == ''


def identity(path):
    metadata = path.stat()
    return metadata.st_dev, metadata.st_ino


def test_validate_lock_rejects_matching_descriptor_without_a_retained_flock(tmp_path):
    transaction = load_transaction_module()
    lock_path = tmp_path / '.venv.install.lock'
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with pytest.raises(RuntimeError, match='no longer held'):
            transaction._validate_lock(descriptor, lock_path, os.geteuid())
    finally:
        os.close(descriptor)


def test_validate_lock_fifo_replacement_is_nonblocking_and_rejected(tmp_path, monkeypatch):
    transaction = load_transaction_module()
    lock_path = tmp_path / '.venv.install.lock'
    descriptor = create_lock(lock_path)
    real_stat = transaction.os.stat
    replaced = False

    def replace_after_named_stat(path, *args, **kwargs):
        nonlocal replaced
        metadata = real_stat(path, *args, **kwargs)
        if not replaced and os.fspath(path) == os.fspath(lock_path):
            replaced = True
            lock_path.unlink()
            os.mkfifo(lock_path, mode=0o600)
        return metadata

    monkeypatch.setattr(transaction.os, 'stat', replace_after_named_stat)
    try:
        with pytest.raises(RuntimeError, match='retention probe'):
            transaction._validate_lock(descriptor, lock_path, os.geteuid())
        assert replaced is True
        assert stat.S_ISFIFO(lock_path.stat().st_mode)
    finally:
        os.close(descriptor)


def test_inspect_accepts_damaged_owned_directory_without_executing_it(tmp_path, monkeypatch):
    transaction = load_transaction_module()
    venv = tmp_path / '.venv'
    child = venv / 'arbitrary-damaged-state'
    child.mkdir(parents=True, mode=0o700)
    (child / 'not-python').write_text('preserve until commit', encoding='utf-8')
    monkeypatch.setattr(transaction, '_contains_submount', lambda _path: False)

    result = transaction._inspect_existing(tmp_path, os.geteuid())

    assert transaction._parse_identity(result) == identity(venv)


def test_inspect_fails_closed_when_walk_reports_an_error(tmp_path, monkeypatch):
    transaction = load_transaction_module()
    (tmp_path / '.venv').mkdir(mode=0o700)
    monkeypatch.setattr(transaction, '_contains_submount', lambda _path: False)

    def broken_walk(_path, *, topdown, onerror, followlinks):
        assert topdown is True
        assert followlinks is False
        onerror(PermissionError('injected traversal failure'))
        return []

    monkeypatch.setattr(transaction.os, 'walk', broken_walk)

    with pytest.raises(RuntimeError, match='completely inspect'):
        transaction._inspect_existing(tmp_path, os.geteuid())


def test_inspect_rejects_a_submount(tmp_path, monkeypatch):
    transaction = load_transaction_module()
    (tmp_path / '.venv').mkdir(mode=0o700)
    monkeypatch.setattr(transaction, '_contains_submount', lambda _path: True)

    with pytest.raises(RuntimeError, match='mount point'):
        transaction._inspect_existing(tmp_path, os.geteuid())


def test_publish_and_rollback_exchange_exact_inodes(tmp_path, monkeypatch):
    transaction = load_transaction_module()
    original = tmp_path / '.venv'
    candidate = tmp_path / '.venv-build.candidate'
    original.mkdir(mode=0o700)
    candidate.mkdir(mode=0o700)
    (original / 'old').write_text('old', encoding='utf-8')
    (candidate / 'new').write_text('new', encoding='utf-8')
    lock_path = tmp_path / '.venv.install.lock'
    lock_fd = create_lock(lock_path)
    monkeypatch.setattr(transaction, '_contains_submount', lambda _path: False)
    original_id = identity(original)
    candidate_id = identity(candidate)
    lock = (lock_fd, str(lock_path), os.geteuid())
    try:
        assert transaction._publish(
            tmp_path,
            candidate.name,
            original_id,
            candidate_id,
            lock,
        ) == transaction.PUBLISH_SENTINEL
        assert identity(original) == candidate_id
        assert identity(candidate) == original_id
        assert transaction._verify_staged(
            tmp_path,
            candidate.name,
            original_id,
            candidate_id,
            lock,
        ) == transaction.STAGED_SENTINEL

        assert transaction._rollback(
            tmp_path,
            candidate.name,
            original_id,
            candidate_id,
            lock,
        ) == transaction.ROLLBACK_SENTINEL
        assert identity(original) == original_id
        assert identity(candidate) == candidate_id
    finally:
        os.close(lock_fd)


def test_verify_staged_rejects_mount_or_submount_before_cleanup(tmp_path, monkeypatch):
    transaction = load_transaction_module()
    installed = tmp_path / '.venv'
    staged = tmp_path / '.venv-build.candidate'
    installed.mkdir(mode=0o700)
    staged.mkdir(mode=0o700)
    lock_path = tmp_path / '.venv.install.lock'
    lock_fd = create_lock(lock_path)
    monkeypatch.setattr(transaction, '_contains_submount', lambda _path: True)
    try:
        assert transaction._verify_staged(
            tmp_path,
            staged.name,
            identity(staged),
            identity(installed),
            (lock_fd, str(lock_path), os.geteuid()),
        ) == transaction.STAGED_PRESERVE_SENTINEL
    finally:
        os.close(lock_fd)


def test_publish_rejects_replaced_lock_inode_before_exchange(tmp_path, monkeypatch):
    transaction = load_transaction_module()
    original = tmp_path / '.venv'
    candidate = tmp_path / '.venv-build.candidate'
    original.mkdir(mode=0o700)
    candidate.mkdir(mode=0o700)
    lock_path = tmp_path / '.venv.install.lock'
    lock_fd = create_lock(lock_path)
    monkeypatch.setattr(transaction, '_contains_submount', lambda _path: False)
    original_id = identity(original)
    candidate_id = identity(candidate)
    try:
        lock_path.unlink()
        replacement = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(replacement)

        with pytest.raises(RuntimeError, match='pathname changed'):
            transaction._publish(
                tmp_path,
                candidate.name,
                original_id,
                candidate_id,
                (lock_fd, str(lock_path), os.geteuid()),
            )
        assert identity(original) == original_id
        assert identity(candidate) == candidate_id
    finally:
        os.close(lock_fd)


def test_publish_rechecks_candidate_identity_immediately_before_rename(tmp_path, monkeypatch):
    transaction = load_transaction_module()
    candidate = tmp_path / '.venv-build.candidate'
    displaced = tmp_path / 'displaced-candidate'
    candidate.mkdir(mode=0o700)
    (candidate / 'verified').write_text('verified', encoding='utf-8')
    candidate_id = identity(candidate)
    lock_path = tmp_path / '.venv.install.lock'
    lock_fd = create_lock(lock_path)

    def replace_during_mount_check(_path):
        candidate.rename(displaced)
        candidate.mkdir(mode=0o700)
        (candidate / 'replacement').write_text('replacement', encoding='utf-8')
        return False

    monkeypatch.setattr(transaction, '_contains_submount', replace_during_mount_check)
    try:
        with pytest.raises(RuntimeError, match='immediately before publication'):
            transaction._publish(
                tmp_path,
                candidate.name,
                None,
                candidate_id,
                (lock_fd, str(lock_path), os.geteuid()),
            )
        assert not (tmp_path / '.venv').exists()
        assert identity(displaced) == candidate_id
        assert identity(candidate) != candidate_id
    finally:
        os.close(lock_fd)
