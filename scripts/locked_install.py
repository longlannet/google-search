import argparse
import ctypes
import fcntl
import hashlib
import importlib.metadata
import os
import re
import signal
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path


MAX_LOCK_BYTES = 4 * 1024 * 1024
F_ADD_SEALS = getattr(fcntl, 'F_ADD_SEALS', 1033)
F_GET_SEALS = getattr(fcntl, 'F_GET_SEALS', 1034)
F_SEAL_SEAL = getattr(fcntl, 'F_SEAL_SEAL', 0x0001)
F_SEAL_SHRINK = getattr(fcntl, 'F_SEAL_SHRINK', 0x0002)
F_SEAL_GROW = getattr(fcntl, 'F_SEAL_GROW', 0x0004)
F_SEAL_WRITE = getattr(fcntl, 'F_SEAL_WRITE', 0x0008)
STABLE_FIELDS = (
    'st_dev',
    'st_ino',
    'st_uid',
    'st_mode',
    'st_nlink',
    'st_size',
    'st_mtime_ns',
    'st_ctime_ns',
)
PIP_INSTALL_TIMEOUT_SECONDS = 300
PIP_TERMINATION_GRACE_SECONDS = 5
CANDIDATE_PROBE_TIMEOUT_SECONDS = 10
MANAGED_INSTALL_SIGNALS = tuple(
    item for item in (getattr(signal, 'SIGHUP', None), signal.SIGINT, signal.SIGTERM)
    if item is not None
)


class _PipInstallInterrupted(BaseException):
    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


class _PipSignalController:
    def __init__(self, previous_mask):
        self.previous_mask = previous_mask
        self.interrupted_by = None

    def handle(self, signum, _frame):
        if self.interrupted_by is not None:
            return
        self.interrupted_by = signum
        raise _PipInstallInterrupted(signum)

    def unblock(self):
        signal.pthread_sigmask(signal.SIG_SETMASK, self.previous_mask)


@contextmanager
def managed_pip_signals():
    if not hasattr(signal, 'pthread_sigmask'):
        raise RuntimeError('pthread signal masks are required for bounded pip cleanup')
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, MANAGED_INSTALL_SIGNALS)
    controller = _PipSignalController(previous_mask)
    previous_handlers = {}
    try:
        for signum in MANAGED_INSTALL_SIGNALS:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, controller.handle)
        yield controller
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, MANAGED_INSTALL_SIGNALS)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _snapshot(metadata, digest):
    return ':'.join((
        str(metadata.st_dev),
        str(metadata.st_ino),
        str(metadata.st_uid),
        str(metadata.st_mode),
        str(metadata.st_nlink),
        str(metadata.st_size),
        str(metadata.st_mtime_ns),
        str(metadata.st_ctime_ns),
        digest,
    ))


def read_verified_lock(base_dir, lock_file, expected_uid, expected_snapshot):
    base = Path(base_dir).resolve(strict=True)
    path = Path(lock_file)
    if path.parent.resolve(strict=True) != base or path.name not in {
        'requirements.txt',
        'requirements-dev.txt',
    }:
        raise RuntimeError('dependency lock path is invalid')

    base_metadata = base.lstat()
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid not in {expected_uid, 0}
        or before.st_mode & 0o022
        or before.st_nlink != 1
        or before.st_dev != base_metadata.st_dev
        or before.st_size > MAX_LOCK_BYTES
    ):
        raise RuntimeError('dependency lock metadata is unsafe')

    flags = (
        os.O_RDONLY
        | getattr(os, 'O_CLOEXEC', 0)
        | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_NONBLOCK', 0)
    )
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError('dependency lock changed before it was opened')
        chunks = []
        total = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_LOCK_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_LOCK_BYTES:
                raise RuntimeError('dependency lock is too large')
            chunks.append(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    if any(getattr(opened, field) != getattr(after, field) for field in STABLE_FIELDS):
        raise RuntimeError('dependency lock changed while it was read')
    named_after = path.lstat()
    if any(getattr(after, field) != getattr(named_after, field) for field in STABLE_FIELDS):
        raise RuntimeError('dependency lock pathname changed while it was read')
    if _snapshot(after, digest.hexdigest()) != expected_snapshot:
        raise RuntimeError('dependency lock no longer matches its initial snapshot')
    return b''.join(chunks)


@contextmanager
def sealed_lock(payload):
    flags = getattr(os, 'MFD_CLOEXEC', 0x0001) | getattr(os, 'MFD_ALLOW_SEALING', 0x0002)
    if hasattr(os, 'memfd_create'):
        descriptor = os.memfd_create('google-search-requirements', flags)
    else:
        libc = ctypes.CDLL(None, use_errno=True)
        memfd_create = getattr(libc, 'memfd_create', None)
        if memfd_create is None:
            raise RuntimeError('Linux sealed memory files are required')
        memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
        memfd_create.restype = ctypes.c_int
        descriptor = memfd_create(b'google-search-requirements', flags)
        if descriptor < 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise RuntimeError('failed to write the sealed dependency lock')
            offset += written
        os.fchmod(descriptor, 0o400)
        seals = (
            F_SEAL_SEAL
            | F_SEAL_SHRINK
            | F_SEAL_GROW
            | F_SEAL_WRITE
        )
        fcntl.fcntl(descriptor, F_ADD_SEALS, seals)
        if fcntl.fcntl(descriptor, F_GET_SEALS) != seals:
            raise RuntimeError('dependency lock seals were not applied')
        os.lseek(descriptor, 0, os.SEEK_SET)
        yield descriptor
    finally:
        os.close(descriptor)


def _expected_distributions(payload, packaging):
    try:
        text = payload.decode('utf-8')
    except UnicodeDecodeError:
        raise RuntimeError('dependency lock is not UTF-8') from None
    expected = {}
    environment = packaging['default_environment']()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(('#', '--')) or line.startswith(('-r ', '-c ')):
            continue
        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_.-]*==', line):
            continue
        if line.endswith('\\'):
            line = line[:-1].rstrip()
        line = re.sub(r'\s+--hash=sha256:[0-9A-Fa-f]{64}(?:\s+.*)?$', '', line)
        try:
            requirement = packaging['Requirement'](line)
        except packaging['InvalidRequirement']:
            raise RuntimeError('dependency lock contains an invalid requirement') from None
        specifiers = list(requirement.specifier)
        if (
            requirement.url
            or len(specifiers) != 1
            or specifiers[0].operator != '=='
            or '*' in specifiers[0].version
        ):
            raise RuntimeError('dependency lock contains an unpinned requirement')
        if requirement.marker and not packaging['Marker'](str(requirement.marker)).evaluate(environment):
            continue
        canonical = packaging['canonicalize_name'](requirement.name)
        if canonical in expected:
            raise RuntimeError('dependency lock contains a duplicate requirement')
        expected[canonical] = specifiers[0].version
    if not expected:
        raise RuntimeError('dependency lock contains no applicable requirements')
    return expected


def _candidate_site_packages(raw_candidate):
    candidate_path = Path(raw_candidate)
    candidate_root = candidate_path.parent.parent.resolve(strict=True)
    if candidate_path.parent.resolve(strict=True) != candidate_root / 'bin':
        raise RuntimeError('candidate interpreter is outside its venv')
    candidate_path.resolve(strict=True)
    environment = os.environ.copy()
    for name in ('SERPER_API_KEY', 'SERPER_API_KEYS', 'BASH_ENV', 'ENV'):
        environment.pop(name, None)
    try:
        version = subprocess.run(
            [
                candidate_path,
                '-I',
                '-B',
                '-S',
                '-c',
                'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")',
            ],
            check=True,
            capture_output=True,
            close_fds=True,
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=CANDIDATE_PROBE_TIMEOUT_SECONDS,
        ).stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError('candidate Python exceeded the bounded version probe timeout') from None
    except subprocess.CalledProcessError:
        raise RuntimeError('candidate Python version probe failed') from None
    if not re.fullmatch(r'3\.(?:10|11|12|13|14)', version):
        raise RuntimeError('candidate Python version is unsupported')

    candidates = list(candidate_root.glob('lib/python*/site-packages'))
    if len(candidates) != 1:
        raise RuntimeError('candidate venv must contain exactly one site-packages directory')
    site_packages = candidates[0].resolve(strict=True)
    expected = (candidate_root / 'lib' / f'python{version}' / 'site-packages').resolve(strict=True)
    if site_packages != expected or not site_packages.is_relative_to(candidate_root):
        raise RuntimeError('candidate site-packages path is inconsistent')
    return site_packages


def _validate_environment(site_packages, payload, packaging):
    expected = _expected_distributions(payload, packaging)
    actual = {}
    for distribution in importlib.metadata.distributions(path=[str(site_packages)]):
        name = distribution.metadata.get('Name')
        if not name:
            raise RuntimeError('installed distribution has no name')
        canonical = packaging['canonicalize_name'](name)
        if canonical in actual:
            raise RuntimeError('installed environment contains a duplicate distribution')
        actual[canonical] = distribution.version
    if actual != expected:
        raise RuntimeError('installed distributions do not match the sealed lock')
    if list(site_packages.glob('*.pth')):
        raise RuntimeError('installed environment contains a startup path hook')
    for entry in site_packages.iterdir():
        if any(
            entry.name == name or entry.name.startswith(f'{name}.')
            for name in ('sitecustomize', 'usercustomize')
        ):
            raise RuntimeError('installed environment contains a startup customization hook')


def _packaging_api():
    from pip._vendor.packaging.markers import Marker, default_environment
    from pip._vendor.packaging.requirements import InvalidRequirement, Requirement
    from pip._vendor.packaging.utils import canonicalize_name

    return {
        'Marker': Marker,
        'default_environment': default_environment,
        'InvalidRequirement': InvalidRequirement,
        'Requirement': Requirement,
        'canonicalize_name': canonicalize_name,
    }


def _terminate_process_group(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=PIP_TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def install_from_sealed_lock(candidate_python, payload):
    site_packages = _candidate_site_packages(candidate_python)
    packaging = _packaging_api()
    import pip

    pip_init = Path(pip.__file__).resolve(strict=True)
    pip_source_root = pip_init.parent.parent
    if pip_init.name != '__init__.py' or pip_init.parent.name != 'pip':
        raise RuntimeError('bootstrap pip package path is invalid')
    runner = (
        'import runpy, sys\n'
        'pip_source_root = sys.argv[1]\n'
        'sys.argv = ["pip", *sys.argv[2:]]\n'
        'sys.path.insert(0, pip_source_root)\n'
        'runpy.run_module("pip", run_name="__main__")\n'
    )
    with sealed_lock(payload) as descriptor:
        lock_path = f'/proc/self/fd/{descriptor}'
        environment = os.environ.copy()
        for name in ('SERPER_API_KEY', 'SERPER_API_KEYS', 'BASH_ENV', 'ENV'):
            environment.pop(name, None)
        environment['PIP_CONFIG_FILE'] = os.devnull
        process = None
        with managed_pip_signals() as signal_controller:
            try:
                process = subprocess.Popen(
                    [
                        str(candidate_python),
                        '-I',
                        '-B',
                        '-X',
                        'pycache_prefix=/dev/null/google-search',
                        '-c',
                        runner,
                        str(pip_source_root),
                        '--isolated',
                        'install',
                        '--disable-pip-version-check',
                        '--no-input',
                        '--require-hashes',
                        '--only-binary=:all:',
                        '-r',
                        lock_path,
                    ],
                    close_fds=True,
                    env=environment,
                    pass_fds=(descriptor,),
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=sys.stderr,
                    stderr=sys.stderr,
                )
                signal_controller.unblock()
                returncode = process.wait(timeout=PIP_INSTALL_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                _terminate_process_group(process)
                raise RuntimeError('pip exceeded the bounded installation timeout') from None
            except _PipInstallInterrupted:
                if process is not None:
                    _terminate_process_group(process)
                raise RuntimeError('pip installation was interrupted by a termination signal') from None
            except BaseException:
                if process is not None:
                    _terminate_process_group(process)
                raise
        if returncode != 0:
            raise RuntimeError('pip rejected the sealed dependency lock')
        _validate_environment(site_packages, payload, packaging)


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--base-dir', required=True)
    parser.add_argument('--lock-file', required=True)
    parser.add_argument('--expected-uid', required=True, type=int)
    parser.add_argument('--expected-snapshot', required=True)
    parser.add_argument('--candidate-python', required=True)
    parser.add_argument('--sentinel', required=True)
    arguments = parser.parse_args(argv)

    payload = read_verified_lock(
        arguments.base_dir,
        arguments.lock_file,
        arguments.expected_uid,
        arguments.expected_snapshot,
    )
    install_from_sealed_lock(arguments.candidate_python, payload)
    print(arguments.sentinel)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError):
        raise SystemExit(1) from None
