import errno
import fcntl
import hashlib
import os
import signal
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import locked_install


def _snapshot(path):
    metadata = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return locked_install._snapshot(metadata, digest)


def test_verified_lock_binds_metadata_and_content(tmp_path):
    lock = tmp_path / 'requirements.txt'
    payload = b'example==1.0 --hash=sha256:' + (b'0' * 64) + b'\n'
    lock.write_bytes(payload)
    lock.chmod(0o600)
    initial_snapshot = _snapshot(lock)

    assert locked_install.read_verified_lock(tmp_path, lock, os.geteuid(), initial_snapshot) == payload

    replacement = tmp_path / 'replacement'
    replacement.write_bytes(payload + b'# changed\n')
    replacement.chmod(0o600)
    os.replace(replacement, lock)
    with pytest.raises(RuntimeError, match='initial snapshot'):
        locked_install.read_verified_lock(tmp_path, lock, os.geteuid(), initial_snapshot)


def test_sealed_lock_survives_named_lock_replacement_and_rejects_writes(tmp_path):
    named_lock = tmp_path / 'requirements.txt'
    original = b'original locked requirements\n'
    named_lock.write_bytes(original)

    with locked_install.sealed_lock(original) as descriptor:
        replacement = tmp_path / 'replacement'
        replacement.write_bytes(b'attacker-controlled requirements\n')
        os.replace(replacement, named_lock)

        os.lseek(descriptor, 0, os.SEEK_SET)
        assert os.read(descriptor, len(original) + 32) == original
        assert fcntl.fcntl(descriptor, locked_install.F_GET_SEALS) & locked_install.F_SEAL_WRITE
        with pytest.raises(OSError) as captured:
            os.write(descriptor, b'x')
        assert captured.value.errno in {errno.EPERM, errno.EBADF}
        assert stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o400


def test_candidate_pip_directly_consumes_sealed_bytes_after_named_lock_replacement(tmp_path):
    wheel_dir = tmp_path / 'wheels'
    wheel_dir.mkdir()
    wheel = wheel_dir / 'sealed_demo-1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('sealed_demo/__init__.py', "VALUE = 'original'\n")
        archive.writestr(
            'sealed_demo-1.0.dist-info/METADATA',
            'Metadata-Version: 2.1\nName: sealed-demo\nVersion: 1.0\n',
        )
        archive.writestr(
            'sealed_demo-1.0.dist-info/WHEEL',
            'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        )
        archive.writestr('sealed_demo-1.0.dist-info/RECORD', '')

    payload = (
        f'--no-index\n--find-links {wheel_dir.as_uri()}\n'
        f'sealed-demo==1.0 --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}\n'
    ).encode()
    named_lock = tmp_path / 'requirements.txt'
    named_lock.write_bytes(payload)
    named_lock.chmod(0o600)
    verified = locked_install.read_verified_lock(
        tmp_path,
        named_lock,
        os.geteuid(),
        _snapshot(named_lock),
    )
    replacement = tmp_path / 'replacement'
    replacement.write_text('attacker-package==9.9\n', encoding='utf-8')
    replacement.chmod(0o600)
    os.replace(replacement, named_lock)

    candidate = tmp_path / '.venv-build.candidate'
    subprocess.run(
        [sys.executable, '-I', '-B', '-m', 'venv', '--without-pip', str(candidate)],
        check=True,
        capture_output=True,
        text=True,
    )
    bootstrap = tmp_path / 'bootstrap'
    subprocess.run(
        [sys.executable, '-I', '-B', '-m', 'venv', str(bootstrap)],
        check=True,
        capture_output=True,
        text=True,
    )
    install = subprocess.run(
        [
            bootstrap / 'bin' / 'python',
            '-I',
            '-B',
            '-c',
            (
                'import sys\n'
                'sys.path.insert(0, sys.argv[1])\n'
                'import locked_install\n'
                'locked_install.install_from_sealed_lock(sys.argv[2], bytes.fromhex(sys.argv[3]))\n'
            ),
            str(SCRIPTS_DIR),
            str(candidate / 'bin' / 'python'),
            verified.hex(),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr

    probe = subprocess.run(
        [
            candidate / 'bin' / 'python',
            '-I',
            '-B',
            '-c',
            'import sealed_demo; print(sealed_demo.VALUE)',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip() == 'original'


def test_candidate_version_probe_has_a_short_normalized_timeout(tmp_path, monkeypatch):
    candidate = tmp_path / '.venv-build.candidate'
    candidate_python = candidate / 'bin' / 'python'
    candidate_python.parent.mkdir(parents=True)
    candidate_python.write_text('not executed', encoding='utf-8')
    observed = {}

    def time_out(*args, **kwargs):
        observed['args'] = args
        observed['kwargs'] = kwargs
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])

    monkeypatch.setattr(locked_install.subprocess, 'run', time_out)

    with pytest.raises(RuntimeError, match='bounded version probe timeout') as captured:
        locked_install._candidate_site_packages(candidate_python)

    assert str(candidate_python) not in str(captured.value)
    assert observed['kwargs']['timeout'] == locked_install.CANDIDATE_PROBE_TIMEOUT_SECONDS
    assert observed['kwargs']['stdin'] is subprocess.DEVNULL
    assert observed['kwargs']['close_fds'] is True
    assert 'SERPER_API_KEY' not in observed['kwargs']['env']
    assert 'SERPER_API_KEYS' not in observed['kwargs']['env']


def test_candidate_version_probe_normalizes_nonzero_exit_without_path(tmp_path, monkeypatch):
    candidate = tmp_path / '.venv-build.secret-candidate'
    candidate_python = candidate / 'bin' / 'python'
    candidate_python.parent.mkdir(parents=True)
    candidate_python.write_text('not executed', encoding='utf-8')

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(23, args[0], stderr='sensitive probe detail')

    monkeypatch.setattr(locked_install.subprocess, 'run', fail)

    with pytest.raises(RuntimeError, match='candidate Python version probe failed') as captured:
        locked_install._candidate_site_packages(candidate_python)

    message = str(captured.value)
    assert str(candidate_python) not in message
    assert 'secret-candidate' not in message
    assert 'sensitive probe detail' not in message


def test_locked_install_cli_hides_failed_candidate_probe_details(tmp_path):
    lock = tmp_path / 'requirements.txt'
    lock.write_text('unused==1.0\n', encoding='utf-8')
    lock.chmod(0o600)
    candidate_python = tmp_path / '.venv-build.secret-candidate' / 'bin' / 'python'
    candidate_python.parent.mkdir(parents=True)
    candidate_python.write_text(
        '#!/bin/sh\nprintf "sensitive candidate stderr\\n" >&2\nexit 23\n',
        encoding='utf-8',
    )
    candidate_python.chmod(0o700)

    completed = subprocess.run(
        [
            sys.executable,
            '-I',
            '-B',
            SCRIPTS_DIR / 'locked_install.py',
            '--base-dir',
            tmp_path,
            '--lock-file',
            lock,
            '--expected-uid',
            str(os.geteuid()),
            '--expected-snapshot',
            _snapshot(lock),
            '--candidate-python',
            candidate_python,
            '--sentinel',
            'must-not-print',
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 1
    assert completed.stdout == ''
    assert completed.stderr == ''


def test_pip_timeout_terminates_and_reaps_the_whole_process_group(tmp_path, monkeypatch):
    waits = []
    signals = []

    class TimedOutProcess:
        pid = 424242

        def poll(self):
            return None

        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) < 3:
                raise subprocess.TimeoutExpired('pip', timeout)
            return -9

    monkeypatch.setattr(locked_install.subprocess, 'Popen', lambda *_args, **_kwargs: TimedOutProcess())
    monkeypatch.setattr(locked_install.os, 'killpg', lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(
        locked_install,
        '_candidate_site_packages',
        lambda _candidate: Path('/unused/site-packages'),
    )
    fake_pip = tmp_path / 'pip' / '__init__.py'
    fake_pip.parent.mkdir()
    fake_pip.write_text('', encoding='utf-8')
    monkeypatch.setitem(sys.modules, 'pip', type('FakePip', (), {'__file__': str(fake_pip)})())
    monkeypatch.setattr(locked_install, '_packaging_api', lambda: {})

    with pytest.raises(RuntimeError, match='bounded installation timeout'):
        locked_install.install_from_sealed_lock(
            '/unused/candidate/python',
            b'unused==1.0\n',
        )

    assert waits == [locked_install.PIP_INSTALL_TIMEOUT_SECONDS,
                     locked_install.PIP_TERMINATION_GRACE_SECONDS, None]
    assert signals == [
        (TimedOutProcess.pid, locked_install.signal.SIGTERM),
        (TimedOutProcess.pid, locked_install.signal.SIGKILL),
    ]


@pytest.mark.parametrize('signum', locked_install.MANAGED_INSTALL_SIGNALS)
def test_pip_termination_signal_reaps_the_process_group_and_restores_handlers(
    tmp_path, monkeypatch, signum,
):
    waits = []
    forwarded = []
    original_handlers = {
        candidate: signal.getsignal(candidate)
        for candidate in locked_install.MANAGED_INSTALL_SIGNALS
    }
    original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())

    class InterruptedProcess:
        pid = 424243

        def poll(self):
            return None

        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) == 1:
                signal.raise_signal(signum)
                raise AssertionError('the signal handler must interrupt the wait')
            return -signal.SIGTERM

    monkeypatch.setattr(locked_install.subprocess, 'Popen', lambda *_args, **_kwargs: InterruptedProcess())
    monkeypatch.setattr(locked_install.os, 'killpg', lambda pid, sent: forwarded.append((pid, sent)))
    monkeypatch.setattr(
        locked_install,
        '_candidate_site_packages',
        lambda _candidate: Path('/unused/site-packages'),
    )
    fake_pip = tmp_path / 'pip' / '__init__.py'
    fake_pip.parent.mkdir()
    fake_pip.write_text('', encoding='utf-8')
    monkeypatch.setitem(sys.modules, 'pip', type('FakePip', (), {'__file__': str(fake_pip)})())
    monkeypatch.setattr(locked_install, '_packaging_api', lambda: {})

    with pytest.raises(RuntimeError, match='termination signal'):
        locked_install.install_from_sealed_lock(
            '/unused/candidate/python',
            b'unused==1.0\n',
        )

    assert waits == [locked_install.PIP_INSTALL_TIMEOUT_SECONDS,
                     locked_install.PIP_TERMINATION_GRACE_SECONDS]
    assert forwarded == [(InterruptedProcess.pid, locked_install.signal.SIGTERM)]
    assert {
        candidate: signal.getsignal(candidate)
        for candidate in locked_install.MANAGED_INSTALL_SIGNALS
    } == original_handlers
    assert signal.pthread_sigmask(signal.SIG_BLOCK, ()) == original_mask


def test_pip_base_exception_reaps_the_process_group(tmp_path, monkeypatch):
    waits = []
    forwarded = []

    class InterruptedProcess:
        pid = 424244

        def poll(self):
            return None

        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) == 1:
                raise KeyboardInterrupt
            return -signal.SIGTERM

    monkeypatch.setattr(locked_install.subprocess, 'Popen', lambda *_args, **_kwargs: InterruptedProcess())
    monkeypatch.setattr(locked_install.os, 'killpg', lambda pid, sent: forwarded.append((pid, sent)))
    monkeypatch.setattr(
        locked_install,
        '_candidate_site_packages',
        lambda _candidate: Path('/unused/site-packages'),
    )
    fake_pip = tmp_path / 'pip' / '__init__.py'
    fake_pip.parent.mkdir()
    fake_pip.write_text('', encoding='utf-8')
    monkeypatch.setitem(sys.modules, 'pip', type('FakePip', (), {'__file__': str(fake_pip)})())
    monkeypatch.setattr(locked_install, '_packaging_api', lambda: {})

    with pytest.raises(KeyboardInterrupt):
        locked_install.install_from_sealed_lock(
            '/unused/candidate/python',
            b'unused==1.0\n',
        )

    assert waits == [locked_install.PIP_INSTALL_TIMEOUT_SECONDS,
                     locked_install.PIP_TERMINATION_GRACE_SECONDS]
    assert forwarded == [(InterruptedProcess.pid, signal.SIGTERM)]
