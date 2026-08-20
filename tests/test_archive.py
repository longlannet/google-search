import io
import json
import os
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = '0.11.6'
LOCK_CUTOFF = '2026-08-19T00:00:00Z'
MAX_RELEASE_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_GIT_ERROR_BYTES = 256 * 1024
MAX_RELEASE_CANDIDATES = 10_000
MAX_RELEASE_ARCHIVE_MEMBERS = 10_000
GIT_ARCHIVE_TIMEOUT_SECONDS = 30
BIDI_CONTROL_CODEPOINTS = frozenset({
    0x061C,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
})
RUNTIME_PACKAGE_NAMES = {
    'certifi',
    'charset-normalizer',
    'idna',
    'requests',
    'urllib3',
}
FORBIDDEN_EXACT = {
    'config/serper.env',
    '.venv.install.lock',
    '.coverage',
    'coverage.xml',
}
FORBIDDEN_PARTS = {
    'runtime',
    'output',
    '.venv',
    'venv',
    '.venv-test',
    '__pycache__',
    '.pytest_cache',
    '.ruff_cache',
    '.mypy_cache',
    '.hypothesis',
    '.tox',
    '.nox',
    '.cache',
    'htmlcov',
    'build',
    'dist',
}
FORBIDDEN_PRIVATE_KEY_NAMES = {
    'id_dsa',
    'id_ecdsa',
    'id_ed25519',
    'id_rsa',
}
FORBIDDEN_CREDENTIAL_BASENAMES = {
    '.netrc',
    '.npmrc',
    '.pypirc',
    'credentials.json',
    'secrets.json',
    'secrets.yaml',
    'secrets.yml',
    'service-account.json',
}
FORBIDDEN_SECRET_SUFFIXES = {'.key', '.p12', '.pem', '.pfx'}
SECRET_ASSIGNMENT_RE = re.compile(
    r'(?im)^\s*SERPER_API_KEYS?\s*=\s*([^#\r\n]+?)\s*$',
)
PRIVATE_KEY_RE = re.compile(
    r'-----BEGIN (?:DSA |EC |OPENSSH |RSA )?PRIVATE KEY-----',
)
LOCK_REQUIREMENT_RE = re.compile(
    r'^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^ ;\\]+)(?:\s+;\s+(.+?))?\s*\\?$',
)
LOCK_HASH_RE = re.compile(r'--hash=sha256:([0-9a-f]{64})(?:\s*\\)?$')
INPUT_PIN_RE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;]+)$')
SAFE_SECRET_PLACEHOLDERS = {
    '...',
    '<redacted>',
    '<secret>',
    'your_real_key_here',
    'your_serper_api_key_here',
}
REPRESENTATIVE_GENERATED_FILES = (
    'config/serper.env',
    '.env',
    '.env.local',
    'private.pem',
    'credentials.key',
    'identity.p12',
    'identity.pfx',
    'id_dsa',
    'id_ecdsa',
    'id_ed25519',
    'id_rsa',
    '.netrc',
    '.npmrc',
    '.pypirc',
    'credentials.json',
    'secrets.json',
    'secrets.yml',
    'secrets.yaml',
    'service-account.json',
    'runtime/serper_rr.idx',
    'output/result.json',
    '.venv/bin/python',
    'venv/bin/python',
    '.venv-test/bin/python',
    '.venv-build.12345678/pyvenv.cfg',
    '.venv-bootstrap.12345678/pyvenv.cfg',
    '.venv-stage.12345678/pyvenv.cfg',
    '.venv.install.lock',
    '__pycache__/root.cpython-310.pyc',
    'scripts/__pycache__/module.cpython-310.pyc',
    '.pytest_cache/state',
    '.ruff_cache/state',
    '.mypy_cache/state',
    '.hypothesis/state',
    '.tox/state',
    '.nox/state',
    '.cache/state',
    '.coverage',
    '.coverage.host.1234.random',
    'coverage.xml',
    'htmlcov/index.html',
    'build/generated.txt',
    'dist/google-search.tar.gz',
    'google_search.egg-info/PKG-INFO',
)


def _git_binary():
    git = shutil.which('git', path='/usr/bin:/bin')
    if git is None:
        pytest.skip('git is required for archive hygiene tests')
    try:
        resolved = Path(git).resolve(strict=True)
        metadata = resolved.stat()
    except OSError:
        pytest.fail('trusted Git executable cannot be resolved')
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.getuid()}
        or metadata.st_mode & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        pytest.fail('trusted Git executable has unsafe metadata')
    return os.fspath(resolved)


def _clean_git_environment(overrides=None):
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith('GIT_') or name in {'SERPER_API_KEY', 'SERPER_API_KEYS'}:
            environment.pop(name, None)
    environment.update({
        'GIT_ATTR_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_NO_LAZY_FETCH': '1',
        'GIT_NO_REPLACE_OBJECTS': '1',
        'GIT_OPTIONAL_LOCKS': '0',
    })
    environment.update(overrides or {})
    return environment


def _git(git, cwd, *args, text=False, env_overrides=None):
    process = subprocess.Popen(
        [git, '--no-replace-objects', '-c', f'core.attributesFile={os.devnull}', *args],
        cwd=cwd,
        env=_clean_git_environment(env_overrides),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    streams = {
        process.stdout.fileno(): ('stdout', process.stdout, MAX_GIT_OUTPUT_BYTES),
        process.stderr.fileno(): ('stderr', process.stderr, MAX_GIT_ERROR_BYTES),
    }
    chunks = {'stdout': [], 'stderr': []}
    totals = {'stdout': 0, 'stderr': 0}
    deadline = time.monotonic() + GIT_ARCHIVE_TIMEOUT_SECONDS
    selector = selectors.DefaultSelector()
    for descriptor in streams:
        os.set_blocking(descriptor, False)
        selector.register(descriptor, selectors.EVENT_READ)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, GIT_ARCHIVE_TIMEOUT_SECONDS)
            events = selector.select(min(remaining, 0.5))
            if not events:
                continue
            for key, _ in events:
                descriptor = key.fd
                label, _stream, limit = streams[descriptor]
                try:
                    chunk = os.read(descriptor, min(64 * 1024, limit + 1 - totals[label]))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(descriptor)
                    continue
                totals[label] += len(chunk)
                if totals[label] > limit:
                    raise AssertionError(f'git {label} exceeded its bounded capture limit')
                chunks[label].append(chunk)
        remaining = max(0.001, deadline - time.monotonic())
        returncode = process.wait(timeout=remaining)
        stdout = b''.join(chunks['stdout'])
        stderr = b''.join(chunks['stderr'])
        if returncode != 0:
            raise subprocess.CalledProcessError(
                returncode,
                process.args,
                output=stdout,
                stderr=stderr,
            )
        return stdout.decode() if text else stdout
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _bounded_git_archive(git, cwd, *args, env_overrides=None):
    process = subprocess.Popen(
        [git, '--no-replace-objects', '-c', f'core.attributesFile={os.devnull}', *args],
        cwd=cwd,
        env=_clean_git_environment(env_overrides),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    assert process.stdout is not None
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    chunks = []
    total = 0
    deadline = time.monotonic() + GIT_ARCHIVE_TIMEOUT_SECONDS
    selector = selectors.DefaultSelector()
    selector.register(descriptor, selectors.EVENT_READ)
    try:
        reached_eof = False
        while not reached_eof:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired('git archive', GIT_ARCHIVE_TIMEOUT_SECONDS)
            events = selector.select(min(remaining, 0.5))
            if not events:
                continue
            try:
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, MAX_RELEASE_ARCHIVE_BYTES + 1 - total),
                )
            except BlockingIOError:
                continue
            if not chunk:
                reached_eof = True
                continue
            total += len(chunk)
            if total > MAX_RELEASE_ARCHIVE_BYTES:
                raise AssertionError('generated release archive is too large')
            chunks.append(chunk)
        remaining = max(0.001, deadline - time.monotonic())
        returncode = process.wait(timeout=remaining)
        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, process.args)
        return b''.join(chunks)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()


def _isolated_commit_archive(git, repository, commit, isolated_git_dir):
    raw_objects = _git(
        git,
        repository,
        'rev-parse',
        '--path-format=absolute',
        '--git-path',
        'objects',
        text=True,
    ).strip()
    object_directory = Path(raw_objects).resolve(strict=True)
    object_metadata = object_directory.lstat()
    if (
        not stat.S_ISDIR(object_metadata.st_mode)
        or object_metadata.st_uid not in {os.getuid(), 0}
        or object_metadata.st_mode & 0o022
    ):
        raise AssertionError('repository object directory metadata is unsafe')

    _git(git, repository, 'init', '--bare', '-q', str(isolated_git_dir))
    isolated_environment = {'GIT_OBJECT_DIRECTORY': str(object_directory)}
    isolated_arguments = ('--git-dir', str(isolated_git_dir))
    resolved = _git(
        git,
        repository,
        *isolated_arguments,
        'rev-parse',
        '--verify',
        f'{commit}^{{commit}}',
        text=True,
        env_overrides=isolated_environment,
    ).strip()
    _git(
        git,
        repository,
        *isolated_arguments,
        'fsck',
        '--strict',
        '--no-reflogs',
        '--no-dangling',
        resolved,
        env_overrides=isolated_environment,
    )
    raw_manifest = _git(
        git,
        repository,
        *isolated_arguments,
        'ls-tree',
        '-r',
        '--name-only',
        '-z',
        resolved,
        env_overrides=isolated_environment,
    )
    if raw_manifest and not raw_manifest.endswith(b'\0'):
        raise AssertionError('commit manifest is not NUL-terminated')
    manifest_names = raw_manifest.split(b'\0')[:-1] if raw_manifest else []
    if len(manifest_names) > MAX_RELEASE_CANDIDATES:
        raise AssertionError('commit manifest contains too many files')
    if len(manifest_names) != len(set(manifest_names)):
        raise AssertionError('commit manifest contains duplicate files')
    archive = _bounded_git_archive(
        git,
        repository,
        *isolated_arguments,
        '-c',
        'tar.umask=0022',
        'archive',
        '--format=tar',
        resolved,
        env_overrides=isolated_environment,
    )
    return resolved, archive


def _read_stable_release_archive(raw_path):
    path = Path(raw_path)
    if not path.is_absolute():
        path = ROOT / path
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid not in {os.getuid(), 0}
        or before.st_mode & 0o022
        or before.st_nlink != 1
        or not 0 < before.st_size <= MAX_RELEASE_ARCHIVE_BYTES
    ):
        raise AssertionError('release archive metadata is unsafe')
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
            raise AssertionError('release archive changed before it was opened')
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_RELEASE_ARCHIVE_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RELEASE_ARCHIVE_BYTES:
                raise AssertionError('release archive is too large')
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    stable_fields = (
        'st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
    )
    named_after = path.lstat()
    if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
        raise AssertionError('release archive changed while it was read')
    if any(getattr(after, field) != getattr(named_after, field) for field in stable_fields):
        raise AssertionError('release archive pathname changed while it was read')
    return b''.join(chunks)


def _read_stable_candidate_file(path, before):
    flags = (
        os.O_RDONLY
        | getattr(os, 'O_CLOEXEC', 0)
        | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_NONBLOCK', 0)
    )
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        stable_fields = (
            'st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size',
            'st_mtime_ns', 'st_ctime_ns',
        )
        if any(getattr(before, field) != getattr(opened, field) for field in stable_fields):
            raise AssertionError('release candidate changed before it was opened')
        chunks = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(64 * 1024, MAX_RELEASE_ARCHIVE_BYTES + 1 - total),
            )
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RELEASE_ARCHIVE_BYTES:
                raise AssertionError('release candidate file is too large')
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    named_after = path.lstat()
    if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
        raise AssertionError('release candidate changed while it was read')
    if any(getattr(after, field) != getattr(named_after, field) for field in stable_fields):
        raise AssertionError('release candidate pathname changed while it was read')
    return b''.join(chunks)


def _normalized(name):
    return name.strip('/').removeprefix('./')


def _canonical_package_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def _is_generated_or_sensitive(name):
    normalized = _normalized(name)
    if normalized in FORBIDDEN_EXACT:
        return True
    path = PurePosixPath(normalized)
    basename = path.name.lower()
    if basename == '.env' or basename.startswith('.env.'):
        return True
    if (
        basename in FORBIDDEN_PRIVATE_KEY_NAMES
        or basename in FORBIDDEN_CREDENTIAL_BASENAMES
        or path.suffix.lower() in FORBIDDEN_SECRET_SUFFIXES
    ):
        return True
    if any(part in FORBIDDEN_PARTS for part in path.parts):
        return True
    if any(
        part.startswith(('.venv-bootstrap.', '.venv-build.', '.venv-stage.'))
        or part.endswith('.egg-info')
        for part in path.parts
    ):
        return True
    return path.suffix in {'.pyc', '.pyo'} or basename.startswith('.coverage.')


def _assert_clean_manifest(names):
    forbidden = sorted(name for name in names if _is_generated_or_sensitive(name))
    assert forbidden == []


def _has_unsafe_path_characters(name):
    for character in name:
        codepoint = ord(character)
        if (
            codepoint < 0x20
            or 0x7F <= codepoint <= 0x9F
            or 0xD800 <= codepoint <= 0xDFFF
            or codepoint in {0x2028, 0x2029}
            or codepoint in BIDI_CONTROL_CODEPOINTS
        ):
            return True
    return False


def _secret_findings(name, data):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return [f'{name}: non-UTF-8 release content']

    findings = []
    if PRIVATE_KEY_RE.search(text):
        findings.append(f'{name}: private-key marker')
    for match in SECRET_ASSIGNMENT_RE.finditer(text):
        value = match.group(1).strip().strip("'\"")
        normalized = value.lower()
        if normalized in SAFE_SECRET_PLACEHOLDERS or normalized.startswith(('example_', 'test_', 'your_')):
            continue
        findings.append(f'{name}: non-placeholder Serper credential assignment')
    return findings


def _source_content_findings(name, data):
    findings = _secret_findings(name, data)
    path = PurePosixPath(_normalized(name))
    if (
        len(path.parts) == 2
        and path.parts[0] == 'scripts'
        and path.suffix == '.py'
        and any(line.startswith(b'#!') for line in data.splitlines())
    ):
        findings.append(f'{name}: non-executable Python module has a shebang')
    return findings


def _expected_file_mode(name):
    path = PurePosixPath(_normalized(name))
    if len(path.parts) == 2 and path.parts[0] == 'scripts' and path.suffix == '.sh':
        return 0o755
    return 0o644


def _assert_safe_candidate_tree(root, names):
    findings = []
    if len(names) > MAX_RELEASE_CANDIDATES:
        findings.append(f'release candidate contains more than {MAX_RELEASE_CANDIDATES} files')
    for name in names:
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith('/')
            or path.as_posix() != name
            or '..' in path.parts
            or any(part in {'', '.'} for part in path.parts)
            or _has_unsafe_path_characters(name)
        ):
            findings.append(f'{name!r}: unsafe candidate path')
    if findings:
        raise AssertionError(findings)
    root_metadata = root.lstat()
    root_device = root_metadata.st_dev
    directories = {root}
    for name in names:
        relative = PurePosixPath(_normalized(name))
        for parent in relative.parents:
            directories.add(root.joinpath(*parent.parts))

    for directory in sorted(directories, key=lambda path: (len(path.parts), str(path))):
        metadata = directory.lstat()
        label = '.' if directory == root else directory.relative_to(root).as_posix()
        if not stat.S_ISDIR(metadata.st_mode):
            findings.append(f'{label}: candidate parent is not a real directory')
            continue
        if metadata.st_uid != os.getuid():
            findings.append(f'{label}: candidate parent is not owned by the current user')
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            findings.append(f'{label}: candidate parent is group/world writable')
        if metadata.st_dev != root_device:
            findings.append(f'{label}: candidate parent crosses a filesystem boundary')

    candidate_metadata = {}
    total_size = 0
    for name in sorted(names):
        path = root / name
        metadata = path.lstat()
        candidate_metadata[name] = metadata
        if not stat.S_ISREG(metadata.st_mode):
            findings.append(f'{name}: not a regular file')
            continue
        total_size += metadata.st_size
        if metadata.st_uid != os.getuid():
            findings.append(f'{name}: not owned by the current user')
        if metadata.st_nlink != 1:
            findings.append(f'{name}: hard-linked source file')
        if metadata.st_dev != root_device:
            findings.append(f'{name}: source file crosses a filesystem boundary')
        actual_mode = stat.S_IMODE(metadata.st_mode)
        expected_mode = _expected_file_mode(name)
        if actual_mode != expected_mode:
            findings.append(f'{name}: mode {actual_mode:04o}, expected {expected_mode:04o}')
    if total_size > MAX_RELEASE_ARCHIVE_BYTES:
        findings.append('release candidate source content exceeds the 64 MiB limit')
    payloads = {}
    if not findings:
        for name in sorted(names):
            payload = _read_stable_candidate_file(root / name, candidate_metadata[name])
            payloads[name] = payload
            findings.extend(_source_content_findings(name, payload))
    assert findings == []
    return payloads


def _assert_safe_archive(bundle):
    findings = []
    seen = set()
    seen_files = set()
    for member_number, member in enumerate(bundle, start=1):
        if member_number > MAX_RELEASE_ARCHIVE_MEMBERS:
            findings.append(f'archive contains more than {MAX_RELEASE_ARCHIVE_MEMBERS} members')
            break
        name = member.name
        path = PurePosixPath(name)
        canonical_name = path.as_posix()
        if (
            not name
            or name.startswith('/')
            or canonical_name == '.'
            or name != canonical_name
            or '..' in path.parts
            or _has_unsafe_path_characters(name)
            or canonical_name in seen
        ):
            findings.append(f'{name!r}: unsafe or duplicate archive path')
            continue
        seen.add(canonical_name)
        if _is_generated_or_sensitive(name):
            findings.append(f'{name}: generated or sensitive path')
        if member.linkname:
            findings.append(f'{name}: archive link target is forbidden')
        if (member.uid, member.gid, member.uname, member.gname) != (0, 0, 'root', 'root'):
            findings.append(f'{name}: noncanonical archive owner')
        if member.isdir():
            if member.mode != 0o755:
                findings.append(f'{name}: directory mode {member.mode:04o}, expected 0755')
            continue
        if not member.isfile():
            findings.append(f'{name}: non-regular archive member')
            continue
        seen_files.add(canonical_name)
        expected_mode = _expected_file_mode(name)
        if member.mode != expected_mode:
            findings.append(f'{name}: mode {member.mode:04o}, expected {expected_mode:04o}')
        extracted = bundle.extractfile(member)
        if extracted is None:
            findings.append(f'{name}: regular member could not be read')
        else:
            findings.extend(_source_content_findings(name, extracted.read()))
    assert findings == []
    return seen_files


def test_explicit_release_archive_is_the_exact_audited_commit_asset(tmp_path):
    archive_name = os.environ.get('GOOGLE_SEARCH_RELEASE_ARCHIVE')
    commit = os.environ.get('GOOGLE_SEARCH_RELEASE_COMMIT')
    if archive_name is None and commit is None:
        return
    assert archive_name and commit, 'archive and commit must be provided together'
    assert re.fullmatch(r'[0-9a-f]{40}', commit), 'release commit must be a full lowercase object ID'

    git = _git_binary()
    resolved, expected = _isolated_commit_archive(
        git,
        ROOT,
        commit,
        tmp_path / 'isolated-audit.git',
    )
    assert resolved == commit
    actual = _read_stable_release_archive(archive_name)
    assert len(expected) <= MAX_RELEASE_ARCHIVE_BYTES
    assert actual == expected, 'release archive bytes do not match the audited commit'
    with tarfile.open(fileobj=io.BytesIO(actual), mode='r:') as bundle:
        _assert_safe_archive(bundle)


def test_isolated_commit_archive_ignores_replace_refs_and_repository_info_attributes(tmp_path):
    git = _git_binary()
    repository = tmp_path / 'source-repository'
    repository.mkdir()
    _git(git, repository, 'init', '-q')

    payload = repository / 'payload.txt'
    payload.write_text('audited content\n', encoding='utf-8')
    _git(git, repository, 'add', 'payload.txt')
    _git(
        git,
        repository,
        '-c',
        'user.name=Archive Test',
        '-c',
        'user.email=archive@example.invalid',
        'commit',
        '-qm',
        'audited',
    )
    audited_commit = _git(git, repository, 'rev-parse', 'HEAD', text=True).strip()

    payload.write_text('replacement content\n', encoding='utf-8')
    _git(
        git,
        repository,
        '-c',
        'user.name=Archive Test',
        '-c',
        'user.email=archive@example.invalid',
        'commit',
        '-am',
        'replacement',
        '-q',
    )
    replacement_commit = _git(git, repository, 'rev-parse', 'HEAD', text=True).strip()
    _git(git, repository, 'replace', audited_commit, replacement_commit)
    info_attributes = repository / '.git' / 'info' / 'attributes'
    info_attributes.write_text('payload.txt export-ignore\n', encoding='utf-8')

    resolved, archive = _isolated_commit_archive(
        git,
        repository,
        audited_commit,
        tmp_path / 'isolated.git',
    )
    assert resolved == audited_commit
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as bundle:
        assert bundle.getnames() == ['payload.txt']
        extracted = bundle.extractfile('payload.txt')
        assert extracted is not None
        assert extracted.read() == b'audited content\n'


def test_isolated_commit_archive_strictly_fscks_reachable_objects(tmp_path):
    git = _git_binary()
    repository = tmp_path / 'corrupt-repository'
    repository.mkdir()
    _git(git, repository, 'init', '-q')
    (repository / 'payload.txt').write_text('audited content\n', encoding='utf-8')
    _git(git, repository, 'add', 'payload.txt')
    _git(
        git,
        repository,
        '-c',
        'user.name=Archive Test',
        '-c',
        'user.email=archive@example.invalid',
        'commit',
        '-qm',
        'audited',
    )
    commit = _git(git, repository, 'rev-parse', 'HEAD', text=True).strip()
    blob = _git(git, repository, 'rev-parse', 'HEAD:payload.txt', text=True).strip()
    object_path = repository / '.git' / 'objects' / blob[:2] / blob[2:]
    object_path.write_bytes(b'corrupt object')

    with pytest.raises(subprocess.CalledProcessError) as captured:
        _isolated_commit_archive(git, repository, commit, tmp_path / 'isolated.git')
    assert 'fsck' in captured.value.cmd


def test_isolated_commit_archive_bounds_the_commit_manifest(tmp_path, monkeypatch):
    git = _git_binary()
    repository = tmp_path / 'large-manifest-repository'
    repository.mkdir()
    _git(git, repository, 'init', '-q')
    for name in ('first.txt', 'second.txt'):
        (repository / name).write_text(name, encoding='utf-8')
    _git(git, repository, 'add', '--all')
    _git(
        git,
        repository,
        '-c',
        'user.name=Archive Test',
        '-c',
        'user.email=archive@example.invalid',
        'commit',
        '-qm',
        'audited',
    )
    commit = _git(git, repository, 'rev-parse', 'HEAD', text=True).strip()
    monkeypatch.setattr(sys.modules[__name__], 'MAX_RELEASE_CANDIDATES', 1)

    with pytest.raises(AssertionError, match='commit manifest contains too many files'):
        _isolated_commit_archive(git, repository, commit, tmp_path / 'isolated.git')


def test_git_capture_and_candidate_count_are_bounded(tmp_path, monkeypatch):
    git = _git_binary()
    repository = tmp_path / 'bounded-git-output'
    repository.mkdir()
    _git(git, repository, 'init', '-q')
    (repository / 'payload.txt').write_bytes(b'x' * 4096)
    _git(git, repository, 'add', 'payload.txt')

    monkeypatch.setattr(sys.modules[__name__], 'MAX_GIT_OUTPUT_BYTES', 128)
    with pytest.raises(AssertionError, match='stdout exceeded'):
        _git(git, repository, 'show', ':payload.txt')

    monkeypatch.setattr(sys.modules[__name__], 'MAX_RELEASE_CANDIDATES', 1)
    (repository / 'second.txt').write_text('second\n', encoding='utf-8')
    with pytest.raises(AssertionError, match='more than 1 files'):
        _assert_safe_candidate_tree(repository, {'payload.txt', 'second.txt'})


def test_clean_git_environment_drops_ambient_serper_credentials(monkeypatch):
    monkeypatch.setenv('SERPER_API_KEY', 'must-not-reach-git')
    monkeypatch.setenv('SERPER_API_KEYS', 'must-not-reach-git-either')
    environment = _clean_git_environment()
    assert 'SERPER_API_KEY' not in environment
    assert 'SERPER_API_KEYS' not in environment


def test_candidate_and_generated_archive_limits_apply_before_unbounded_reads(tmp_path, monkeypatch):
    git = _git_binary()
    repository = tmp_path / 'bounded-repository'
    repository.mkdir()
    _git(git, repository, 'init', '-q')
    payload = repository / 'payload.txt'
    payload.write_bytes(b'x' * 4096)
    payload.chmod(_expected_file_mode('payload.txt'))
    _git(git, repository, 'add', 'payload.txt')
    tree = _git(git, repository, 'write-tree', text=True).strip()

    monkeypatch.setattr(sys.modules[__name__], 'MAX_RELEASE_ARCHIVE_BYTES', 1024)
    with pytest.raises(AssertionError, match='source content exceeds'):
        _assert_safe_candidate_tree(repository, {'payload.txt'})
    with pytest.raises(AssertionError, match='generated release archive is too large'):
        _bounded_git_archive(
            git,
            repository,
            '-c',
            'tar.umask=0022',
            'archive',
            '--format=tar',
            tree,
        )


def _parse_lock(lock_name):
    text = (ROOT / lock_name).read_text(encoding='utf-8')
    lines = text.splitlines()
    starts = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if line and not line[0].isspace() and not line.startswith('#'):
            match = LOCK_REQUIREMENT_RE.fullmatch(line)
            assert match is not None, f'{lock_name}:{index + 1}: unsafe or unpinned requirement: {line}'
            starts.append((index, match))
            continue
        assert LOCK_HASH_RE.fullmatch(stripped) is not None, (
            f'{lock_name}:{index + 1}: unexpected requirement continuation: {line}'
        )
    assert starts, lock_name

    entries = {}
    for position, (start, match) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        name = _canonical_package_name(match.group(1))
        marker = (match.group(3) or '').strip()
        key = (name, marker)
        assert key not in entries, f'{lock_name}: duplicate lock entry {key}'
        hashes = {
            hash_match.group(1)
            for line in lines[start + 1:end]
            if (hash_match := LOCK_HASH_RE.search(line.strip())) is not None
        }
        assert hashes, f'{lock_name}: {name} is missing SHA-256 hashes'
        entries[key] = (match.group(2), frozenset(hashes))
    return entries


def _parse_input(input_name):
    pins = {}
    includes = []
    for line_number, raw_line in enumerate((ROOT / input_name).read_text(encoding='utf-8').splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('-r '):
            includes.append(line[3:].strip())
            continue
        match = INPUT_PIN_RE.fullmatch(line)
        assert match is not None, f'{input_name}:{line_number}: direct inputs must be exact pins'
        name = _canonical_package_name(match.group(1))
        assert name not in pins, f'{input_name}:{line_number}: duplicate direct pin'
        pins[name] = match.group(2)
    return pins, includes


def _current_candidates(git):
    raw_names = _git(
        git,
        ROOT,
        'ls-files',
        '--cached',
        '--others',
        '--exclude-standard',
        '-z',
    ).split(b'\0')
    assert raw_names[-1] == b'', 'Git candidate manifest is not NUL-terminated'
    names = [os.fsdecode(name) for name in raw_names[:-1]]
    assert len(names) <= MAX_RELEASE_CANDIDATES, 'Git candidate manifest contains too many files'
    assert len(names) == len(set(names)), 'Git candidate manifest contains duplicate files'
    return set(names)


def _archive_candidate_tree(git, candidate_payloads, destination):
    destination.mkdir()
    for name, payload in sorted(candidate_payloads.items()):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(payload)
        target.chmod(_expected_file_mode(name))
    _git(git, destination, 'init', '-q')
    _git(git, destination, 'add', '--all', '--force')
    tree = _git(git, destination, 'write-tree', text=True).strip()
    return _bounded_git_archive(
        git,
        destination,
        '-c',
        'tar.umask=0022',
        'archive',
        '--format=tar',
        tree,
    )


def test_gitignore_builds_a_clean_file_manifest_and_archive(tmp_path):
    git = _git_binary()
    repo = tmp_path / 'archive-fixture'
    repo.mkdir()
    shutil.copy2(ROOT / '.gitignore', repo / '.gitignore')
    (repo / 'README.md').write_text('allowed\n', encoding='utf-8')
    for relative in REPRESENTATIVE_GENERATED_FILES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('must not ship\n', encoding='utf-8')

    _git(git, repo, 'init', '-q')
    _git(git, repo, 'add', '--all')
    manifest = set(_git(git, repo, 'ls-files', '-z').decode().split('\0')) - {''}
    assert {'.gitignore', 'README.md'} <= manifest
    _assert_clean_manifest(manifest)

    tree = _git(git, repo, 'write-tree', text=True).strip()
    archive = _bounded_git_archive(
        git,
        repo,
        '-c',
        'tar.umask=0022',
        'archive',
        '--format=tar',
        tree,
    )
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as bundle:
        archived_names = _assert_safe_archive(bundle)
    assert {'.gitignore', 'README.md'} <= archived_names
    _assert_clean_manifest(archived_names)


def test_archive_git_selection_ignores_ambient_path(tmp_path, monkeypatch):
    hostile = tmp_path / 'git'
    hostile.write_text('#!/bin/sh\nexit 99\n', encoding='utf-8')
    hostile.chmod(0o755)
    monkeypatch.setenv('PATH', os.fspath(tmp_path))

    selected = Path(_git_binary())

    assert selected != hostile
    assert selected.is_absolute()
    assert selected.is_file()


def test_current_repository_release_candidate_is_safe(tmp_path):
    git = _git_binary()
    probe = subprocess.run(
        [git, 'rev-parse', '--is-inside-work-tree'],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != 'true':
        pytest.skip('source tree is not a Git checkout')
    candidates = _current_candidates(git)
    _assert_clean_manifest(candidates)
    candidate_payloads = _assert_safe_candidate_tree(ROOT, candidates)

    archive = _archive_candidate_tree(git, candidate_payloads, tmp_path / 'release-candidate')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as bundle:
        archived_files = _assert_safe_archive(bundle)
    assert archived_files == candidates


@pytest.mark.parametrize(
    ('member_type', 'mode'),
    [
        (tarfile.SYMTYPE, 0o777),
        (tarfile.LNKTYPE, 0o644),
        (tarfile.CHRTYPE, 0o600),
        (tarfile.FIFOTYPE, 0o600),
        (tarfile.REGTYPE, 0o666),
        (tarfile.REGTYPE, 0o755),
    ],
)
def test_archive_policy_rejects_links_special_files_and_unsafe_modes(member_type, mode):
    member = tarfile.TarInfo('allowed.txt')
    member.type = member_type
    member.mode = mode
    member.size = 0
    member.uid = 0
    member.gid = 0
    member.uname = 'root'
    member.gname = 'root'
    if member_type in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
        member.linkname = 'target'
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as bundle:
        bundle.addfile(member, io.BytesIO())
    stream.seek(0)
    with tarfile.open(fileobj=stream, mode='r:') as bundle, pytest.raises(AssertionError):
        _assert_safe_archive(bundle)


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('uid', 1000),
        ('gid', 1000),
        ('uname', 'builder'),
        ('gname', 'builder'),
    ],
)
def test_archive_policy_rejects_noncanonical_owner(field, value):
    member = tarfile.TarInfo('allowed.txt')
    member.mode = 0o644
    member.size = 0
    member.uid = 0
    member.gid = 0
    member.uname = 'root'
    member.gname = 'root'
    setattr(member, field, value)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as bundle:
        bundle.addfile(member, io.BytesIO())
    stream.seek(0)
    with tarfile.open(fileobj=stream, mode='r:') as bundle, pytest.raises(AssertionError):
        _assert_safe_archive(bundle)


@pytest.mark.parametrize(
    'name',
    [
        '/absolute.txt',
        '../escape.txt',
        'nested/../escape.txt',
        './ambiguous.txt',
        'nested//ambiguous.txt',
        'line\nbreak.txt',
    ],
)
def test_archive_policy_rejects_unsafe_paths(name):
    member = tarfile.TarInfo(name)
    member.mode = 0o644
    member.size = 0
    member.uid = 0
    member.gid = 0
    member.uname = 'root'
    member.gname = 'root'
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as bundle:
        bundle.addfile(member, io.BytesIO())
    stream.seek(0)
    with tarfile.open(fileobj=stream, mode='r:') as bundle, pytest.raises(AssertionError):
        _assert_safe_archive(bundle)


@pytest.mark.parametrize(
    'unsafe_character',
    ['\x00', '\x1f', '\x7f', '\x85', '\u061c', '\u200e', '\u2028', '\u2029', '\u202e', '\u2066', '\udcff'],
)
def test_archive_policy_rejects_terminal_unsafe_and_non_utf8_paths(unsafe_character):
    member = tarfile.TarInfo(f'unsafe{unsafe_character}name.txt')

    with pytest.raises(AssertionError, match='unsafe or duplicate archive path'):
        _assert_safe_archive([member])


def test_archive_policy_rejects_duplicate_paths():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as bundle:
        for _ in range(2):
            member = tarfile.TarInfo('duplicate.txt')
            member.mode = 0o644
            member.size = 0
            member.uid = 0
            member.gid = 0
            member.uname = 'root'
            member.gname = 'root'
            bundle.addfile(member, io.BytesIO())
    stream.seek(0)
    with tarfile.open(fileobj=stream, mode='r:') as bundle, pytest.raises(AssertionError):
        _assert_safe_archive(bundle)


def test_archive_policy_stops_after_the_member_limit(monkeypatch):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as bundle:
        for name in ('first.txt', 'second.txt'):
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            member.size = 0
            member.uid = 0
            member.gid = 0
            member.uname = 'root'
            member.gname = 'root'
            bundle.addfile(member, io.BytesIO())
    stream.seek(0)
    monkeypatch.setattr(sys.modules[__name__], 'MAX_RELEASE_ARCHIVE_MEMBERS', 1)
    with tarfile.open(fileobj=stream, mode='r:') as bundle, pytest.raises(AssertionError, match='more than 1'):
        _assert_safe_archive(bundle)


def test_candidate_policy_rejects_symlinks_hardlinks_and_secret_content(tmp_path):
    regular = tmp_path / 'regular.txt'
    regular.write_text('allowed\n', encoding='utf-8')
    linked = tmp_path / 'linked.txt'
    os.link(regular, linked)
    with pytest.raises(AssertionError):
        _assert_safe_candidate_tree(tmp_path, {'regular.txt', 'linked.txt'})

    linked.unlink()
    regular.unlink()
    regular.symlink_to('missing')
    with pytest.raises(AssertionError):
        _assert_safe_candidate_tree(tmp_path, {'regular.txt'})

    regular.unlink()
    secret_name = 'secret.txt'
    secret_value = 'SERPER_' + 'API_KEY=live_nonplaceholder_value'
    (tmp_path / secret_name).write_text(secret_value + '\n', encoding='utf-8')
    with pytest.raises(AssertionError):
        _assert_safe_candidate_tree(tmp_path, {secret_name})


def test_candidate_policy_rejects_writable_or_symlinked_parent_directories(tmp_path):
    writable = tmp_path / 'writable'
    writable.mkdir()
    candidate = writable / 'allowed.txt'
    candidate.write_text('allowed\n', encoding='utf-8')
    writable.chmod(0o777)
    with pytest.raises(AssertionError):
        _assert_safe_candidate_tree(tmp_path, {'writable/allowed.txt'})

    writable.chmod(0o755)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'allowed.txt').write_text('allowed\n', encoding='utf-8')
    linked = tmp_path / 'linked'
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(AssertionError):
        _assert_safe_candidate_tree(tmp_path, {'linked/allowed.txt'})


@pytest.mark.parametrize(
    'name',
    [
        '../escape.txt',
        './ambiguous.txt',
        'nested//ambiguous.txt',
        'line\nbreak.txt',
        'control\x85name.txt',
        'line\u2028separator.txt',
        'paragraph\u2029separator.txt',
        'override\u202ename.txt',
        'isolate\u2066name.txt',
        'surrogate\udcffname.txt',
    ],
)
def test_candidate_policy_rejects_unsafe_paths_before_opening(tmp_path, name):
    with pytest.raises(AssertionError, match='unsafe candidate path'):
        _assert_safe_candidate_tree(tmp_path, {name})


def test_candidate_archive_uses_the_stably_read_payload(tmp_path):
    git = _git_binary()
    source = tmp_path / 'source'
    source.mkdir()
    payload = source / 'payload.txt'
    payload.write_text('audited content\n', encoding='utf-8')
    payload.chmod(_expected_file_mode('payload.txt'))
    candidate_payloads = _assert_safe_candidate_tree(source, {'payload.txt'})

    payload.write_text('replacement content\n', encoding='utf-8')
    archive = _archive_candidate_tree(git, candidate_payloads, tmp_path / 'candidate')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as bundle:
        extracted = bundle.extractfile('payload.txt')
        assert extracted is not None
        assert extracted.read() == b'audited content\n'


def test_config_setup_docs_use_noclobber_and_never_truncate_existing_keys():
    for name in ('README.md', 'config/serper.env.example'):
        text = (ROOT / name).read_text(encoding='utf-8')
        assert 'install -m 600 /dev/null config/serper.env' not in text
        assert 'set -o noclobber' in text
        assert ': > config/serper.env' in text


def test_openclaw_metadata_declares_every_daily_entrypoint_binary():
    frontmatter = (ROOT / 'SKILL.md').read_text(encoding='utf-8').split('---', 2)[1]
    metadata_line = next(line for line in frontmatter.splitlines() if line.startswith('metadata: '))
    metadata = json.loads(metadata_line.removeprefix('metadata: '))['openclaw']
    assert metadata['os'] == ['linux']
    assert metadata['requires']['bins'] == [
        'bash', 'python3', 'find', 'stat', 'readlink', 'id',
        'dirname', 'printf', 'sha256sum', 'sort',
    ]
    assert metadata['requires']['env'] == ['SERPER_API_KEY']
    assert metadata['primaryEnv'] == 'SERPER_API_KEY'


def test_ci_runs_the_complete_gate_with_the_matrix_local_venv():
    workflow = (ROOT / '.github/workflows/test.yml').read_text(encoding='utf-8')
    assert 'timeout-minutes: 90' in workflow
    assert 'python -m venv --without-pip .venv' in workflow
    assert 'python -m venv "$RUNNER_TEMP/google-search-bootstrap"' in workflow
    assert '-m pip --isolated --python "$PWD/.venv/bin/python"' in workflow
    assert 'importlib.util.find_spec("pip") is None' in workflow
    assert 'importlib.util.find_spec("setuptools") is None' in workflow
    assert 'find .venv/lib -type f -name \'*.pth\'' in workflow
    assert "-name 'pip-*.dist-info'" in workflow
    assert '.venv/bin/python -I -S -c' in workflow
    assert workflow.index("find .venv/lib -type f -name '*.pth'") < workflow.index(
        '.venv/bin/python -I -S -c'
    )
    assert '.venv/bin/python -m pip' not in workflow
    assert 'PIP_CONFIG_FILE: /dev/null' in workflow
    assert 'SHELLCHECK_VERSION: 0.11.0' in workflow
    assert 'SHELLCHECK_SHA256: 8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198' in workflow
    assert '--connect-timeout 10 --max-time 60' in workflow
    assert "printf '%s  %s\\n' \"$SHELLCHECK_SHA256\" \"$archive\" | sha256sum --check --strict -" in workflow
    assert 'pinned_shellcheck="$RUNNER_TEMP/shellcheck-bin/shellcheck"' in workflow
    assert 'test "$(command -v shellcheck)" = "$pinned_shellcheck"' in workflow
    assert "grep -F 'version: 0.11.0'" in workflow
    assert '"$pinned_shellcheck" --norc -x scripts/*.sh' in workflow
    assert "' references/releasing.md | \"$pinned_shellcheck\" --norc --shell=bash -" in workflow
    assert '/usr/bin/shellcheck' not in workflow
    assert 'run: /bin/bash -p scripts/check.sh --venv' in workflow
    assert 'run: python -m pytest' not in workflow


def test_release_recipe_is_private_bounded_and_uses_the_clean_committed_gate():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    recipe = readme.split('候选归档必须来自', 1)[1].split('```bash', 1)[1].split('```', 1)[0]
    assert recipe.lstrip().startswith("/bin/bash -p <<'GOOGLE_SEARCH_RELEASE_RECIPE'")
    assert recipe.rstrip().endswith('GOOGLE_SEARCH_RELEASE_RECIPE')
    assert 'PATH=/usr/bin:/bin' in recipe
    assert 'export PATH TMPDIR TMP TEMP' in recipe
    assert 'TMPDIR=/tmp\nTMP=/tmp\nTEMP=/tmp' in recipe
    assert "stat -c '%u:%a' -- /tmp" in recipe
    assert 'for variable in "${!LD_@}"; do' in recipe
    assert 'unset GLIBC_TUNABLES GCONV_PATH' in recipe
    assert 'unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE' in recipe
    assert 'trap cleanup_release_recipe EXIT\n' in recipe
    assert 'private_release_recipe_directory_path_is_valid()' in recipe
    assert 'remove_private_release_recipe_directory()' in recipe
    assert 'find -P "$directory" -xdev -depth -delete' in recipe
    assert 'rm -rf -- "$AUDIT_ROOT"' not in recipe
    assert 'rm -rf -- "$RELEASE_DIR"' not in recipe
    assert "trap 'exit_for_release_signal 129' HUP" in recipe
    assert "trap 'exit_for_release_signal 130' INT" in recipe
    assert "trap 'exit_for_release_signal 143' TERM" in recipe
    assert 'set -o noclobber' in recipe
    assert 'unset SERPER_API_KEY SERPER_API_KEYS' in recipe
    assert 'GIT_*|PYTEST_*) unset -v "$variable"' in recipe
    assert 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1' in recipe
    assert 'ls-files --cached -v -z' in recipe
    assert "case \"$index_entry\" in 'H '*" in recipe
    assert 'diff-index --cached --quiet --no-ext-diff --no-textconv "$commit" --' in recipe
    assert 'assert_index_worktree_blobs "$snapshot_prefix"' in recipe
    assert 'ls-files --stage -z' in recipe
    assert 'hash-object --no-filters -- "$tracked_path"' in recipe
    assert '[ "$actual_oid" = "$expected_oid" ]' in recipe
    assert '100644) expected_permissions=644' in recipe
    assert '100755) expected_permissions=755' in recipe
    assert 'diff-files' not in recipe
    assert 'assert_repository_parent_chain_safe' in recipe
    assert 'assert_tracked_directory_safe' in recipe
    assert recipe.count('assert_bootstrap_directory_trust ') >= 2
    device_assignment = 'REPOSITORY_DEVICE="$(stat -c \'%d\' -- "$REPOSITORY_ROOT")"'
    assert device_assignment in recipe
    assert recipe.index(device_assignment) < recipe.index(
        'assert_bootstrap_clean_worktree "$AUDIT_ROOT/bootstrap" "$COMMIT"'
    )
    assert 'ls-tree -r --full-tree -z "$commit"' in recipe
    assert 'ls-files --stage -z' in recipe
    assert '/bin/bash -p scripts/run.sh --venv --quiet --runtime-info' in recipe
    assert '--expect-runtime-token "$RELEASE_RUNTIME_TOKEN"' in recipe
    assert '--task check-release-source --' in recipe
    assert "google-search-release-source-ok-v1" in recipe
    assert '/usr/bin/python3' not in recipe
    assert 'ls-files --others --ignored --exclude-standard -z' in recipe
    assert "scripts tests ':(top,glob)*.py'" in recipe
    assert recipe.count('assert_clean_worktree ') >= 2
    bootstrap_call = 'assert_bootstrap_clean_worktree "$AUDIT_ROOT/bootstrap" "$COMMIT"'
    assert bootstrap_call in recipe
    assert recipe.index(bootstrap_call) < recipe.index('\nselect_release_runtime\n')
    assert 'fsck --strict --no-reflogs --no-dangling' in recipe
    assert 'ls-tree -r --name-only -z' in recipe
    assert 'MANIFEST_COUNT' in recipe and '-le 10000' in recipe
    assert 'bounded_git_capture "$ARCHIVE" 67108864' in recipe
    assert 'timeout --signal=TERM --kill-after=5s 30s' in recipe
    assert 'timeout --signal=TERM --kill-after=5s 5400s' in recipe
    assert '/bin/bash -p scripts/check.sh --venv' in recipe
    assert '--release-archive "$ARCHIVE" --release-commit "$COMMIT"' in recipe
    assert '.venv/bin/python -I -B -m pytest' not in recipe
    assert '/dist/' not in recipe and 'install -d -m 700 dist' not in recipe


def test_release_recipe_cleanup_path_guard_rejects_traversal_and_noncanonical_paths():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    recipe = readme.split('候选归档必须来自', 1)[1].split('```bash', 1)[1].split('```', 1)[0]
    start = recipe.index('private_release_recipe_directory_path_is_valid() {')
    end = recipe.index('\n}\ncleanup_release_recipe()', start) + 3
    guard = recipe[start:end]

    for rejected in (
        '/tmp/google-search-release./../..///',
        '/tmp/google-search-audit.ABCDEFG/',
        '/tmp/google-search-release.ABC/../DEFG',
        '/tmp/google-search-audit.ABCDEFGH/child',
        '/tmp/google-search-release.ABCDEFG!',
    ):
        result = subprocess.run(
            ['/bin/bash', '-c', guard + '\nprivate_release_recipe_directory_path_is_valid "$1"', '_', rejected],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0, rejected

    for allowed in (
        '/tmp/google-search-audit.A1b2C3d4',
        '/tmp/google-search-release.A1b2C3d4',
    ):
        result = subprocess.run(
            ['/bin/bash', '-c', guard + '\nprivate_release_recipe_directory_path_is_valid "$1"', '_', allowed],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, allowed


@pytest.mark.parametrize('mutation', ('unstaged', 'staged'))
def test_release_recipe_rejects_a_modified_runner_before_executing_it(tmp_path, mutation):
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    recipe = readme.split('候选归档必须来自', 1)[1].split('```bash', 1)[1].split('```', 1)[0]
    git = _git_binary()
    repository = tmp_path / 'release-bootstrap'
    scripts = repository / 'scripts'
    scripts.mkdir(parents=True)
    runner = scripts / 'run.sh'
    runner.write_text('#!/bin/bash -p\nexit 97\n', encoding='utf-8')
    runner.chmod(0o755)
    _git(git, repository, 'init', '-q')
    _git(git, repository, 'add', 'scripts/run.sh')
    _git(
        git,
        repository,
        '-c',
        'user.name=Release Bootstrap Test',
        '-c',
        'user.email=release-bootstrap@example.invalid',
        'commit',
        '-qm',
        'trusted runner',
    )

    marker = tmp_path / f'{mutation}-runner-executed'
    runner.write_text(
        '#!/bin/bash -p\n'
        ': >"$RELEASE_RUNNER_MARKER"\n'
        'exit 97\n',
        encoding='utf-8',
    )
    if mutation == 'staged':
        _git(git, repository, 'add', 'scripts/run.sh')
    environment = os.environ.copy()
    environment['RELEASE_RUNNER_MARKER'] = os.fspath(marker)

    result = subprocess.run(
        ['/bin/bash', '-p', '-c', recipe],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert not marker.exists(), (result.stdout, result.stderr)


def test_release_recipe_hashes_worktree_blobs_instead_of_trusting_git_stat_cache(tmp_path):
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    recipe = readme.split('候选归档必须来自', 1)[1].split('```bash', 1)[1].split('```', 1)[0]
    git = _git_binary()
    repository = tmp_path / 'release-stat-cache'
    scripts = repository / 'scripts'
    scripts.mkdir(parents=True)
    runner = scripts / 'run.sh'
    malicious = (
        '#!/bin/bash -p\n'
        ': >"$RELEASE_RUNNER_MARKER"\n'
        'exit 97\n'
    )
    trusted_prefix = '#!/bin/bash -p\n#'
    trusted_suffix = '\nexit 97\n'
    trusted = trusted_prefix + ('x' * (len(malicious) - len(trusted_prefix) - len(trusted_suffix))) + trusted_suffix
    assert len(trusted.encode()) == len(malicious.encode())
    runner.write_text(trusted, encoding='utf-8')
    runner.chmod(0o755)
    old_time_ns = time.time_ns() - 5_000_000_000
    os.utime(runner, ns=(old_time_ns, old_time_ns))
    _git(git, repository, 'init', '-q')
    _git(git, repository, 'add', 'scripts/run.sh')
    _git(
        git,
        repository,
        '-c',
        'user.name=Release Stat Cache Test',
        '-c',
        'user.email=release-stat-cache@example.invalid',
        'commit',
        '-qm',
        'trusted runner',
    )
    indexed_stat = runner.stat()
    _git(git, repository, 'config', 'core.trustctime', 'false')
    _git(git, repository, 'config', 'core.checkStat', 'minimal')
    runner.write_text(malicious, encoding='utf-8')
    runner.chmod(0o755)
    os.utime(runner, ns=(indexed_stat.st_atime_ns, indexed_stat.st_mtime_ns))

    _git(git, repository, 'diff-files', '--quiet', '--no-ext-diff', '--no-textconv', '--')
    marker = tmp_path / 'stat-cache-runner-executed'
    environment = os.environ.copy()
    environment['RELEASE_RUNNER_MARKER'] = os.fspath(marker)

    result = subprocess.run(
        ['/bin/bash', '-p', '-c', recipe],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert not marker.exists(), (result.stdout, result.stderr)


def test_release_recipe_never_executes_a_repository_clean_filter(tmp_path):
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    recipe = readme.split('候选归档必须来自', 1)[1].split('```bash', 1)[1].split('```', 1)[0]
    git = _git_binary()
    repository = tmp_path / 'release-clean-filter'
    scripts = repository / 'scripts'
    scripts.mkdir(parents=True)
    runner = scripts / 'run.sh'
    runner.write_text('#!/bin/bash -p\nexit 97\n', encoding='utf-8')
    runner.chmod(0o755)
    _git(git, repository, 'init', '-q')
    _git(git, repository, 'add', 'scripts/run.sh')
    _git(
        git,
        repository,
        '-c',
        'user.name=Release Filter Test',
        '-c',
        'user.email=release-filter@example.invalid',
        'commit',
        '-qm',
        'trusted runner',
    )

    marker = tmp_path / 'clean-filter-executed'
    filter_program = tmp_path / 'malicious-clean-filter'
    filter_program.write_text(
        f'#!/bin/sh\n: >{marker}\ncat\n',
        encoding='utf-8',
    )
    filter_program.chmod(0o700)
    info_attributes = repository / '.git' / 'info' / 'attributes'
    info_attributes.write_text('scripts/run.sh filter=release-bootstrap\n', encoding='utf-8')
    _git(git, repository, 'config', 'filter.release-bootstrap.required', 'true')
    _git(git, repository, 'config', 'filter.release-bootstrap.clean', os.fspath(filter_program))
    attributes = _git(git, repository, 'check-attr', 'filter', '--', 'scripts/run.sh', text=True)
    assert attributes.endswith(': release-bootstrap\n')
    current = runner.stat()
    index = repository / '.git' / 'index'
    index_stat = index.stat()
    os.utime(index, ns=(index_stat.st_atime_ns, current.st_mtime_ns - 2_000_000_000))
    try:
        _git(git, repository, 'diff-files', '--quiet', '--no-ext-diff', '--no-textconv', '--')
    except subprocess.CalledProcessError:
        pass
    assert marker.is_file(), 'the hostile clean filter fixture was not exercised'
    marker.unlink()

    result = subprocess.run(
        ['/bin/bash', '-p', '-c', recipe],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert not marker.exists(), (result.stdout, result.stderr)


def _make_release_runner_probe(tmp_path, name):
    git = _git_binary()
    repository = tmp_path / name
    scripts = repository / 'scripts'
    scripts.mkdir(parents=True)
    marker = tmp_path / f'{name}-runner-executed'
    runner = scripts / 'run.sh'
    runner.write_text(
        '#!/bin/bash -p\n'
        ': >"$RELEASE_RUNNER_MARKER"\n'
        'exit 97\n',
        encoding='utf-8',
    )
    runner.chmod(0o755)
    _git(git, repository, 'init', '-q')
    _git(git, repository, 'add', 'scripts/run.sh')
    _git(
        git,
        repository,
        '-c',
        'user.name=Release Directory Test',
        '-c',
        'user.email=release-directory@example.invalid',
        'commit',
        '-qm',
        'trusted runner',
    )
    return repository, scripts, marker


def _run_release_runner_probe(repository, marker):
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    recipe = readme.split('候选归档必须来自', 1)[1].split('```bash', 1)[1].split('```', 1)[0]
    environment = os.environ.copy()
    environment['RELEASE_RUNNER_MARKER'] = os.fspath(marker)
    return subprocess.run(
        ['/bin/bash', '-p', '-c', recipe],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_release_recipe_reaches_runner_in_a_trusted_checkout(tmp_path):
    repository, _scripts, marker = _make_release_runner_probe(tmp_path, 'trusted-release-repository')

    result = _run_release_runner_probe(repository, marker)

    assert result.returncode != 0
    assert marker.is_file(), (result.stdout, result.stderr)


def test_release_recipe_rejects_a_writable_tracked_directory_before_runner(tmp_path):
    repository, scripts, marker = _make_release_runner_probe(tmp_path, 'writable-release-directory')
    scripts.chmod(0o775)

    result = _run_release_runner_probe(repository, marker)

    assert result.returncode != 0
    assert not marker.exists(), (result.stdout, result.stderr)


@pytest.mark.skipif(os.geteuid() != 0, reason='foreign-owned release paths require root')
@pytest.mark.parametrize('foreign_boundary', ('parent', 'scripts'))
def test_release_recipe_rejects_foreign_owned_directories_before_runner(
    tmp_path,
    foreign_boundary,
):
    repository, scripts, marker = _make_release_runner_probe(tmp_path, 'foreign-release-parent/repository')
    foreign_uid = next(uid for uid in (65534, 65533, 12345) if uid != os.geteuid())
    boundary = repository.parent if foreign_boundary == 'parent' else scripts
    os.chown(boundary, foreign_uid, -1)

    result = _run_release_runner_probe(repository, marker)

    assert result.returncode != 0
    assert not marker.exists(), (result.stdout, result.stderr)


def test_release_runbook_requires_signed_immutable_verified_publication():
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    required_fragments = (
        'C678256ACBFC6491BF5076655F3AE24999921FFC',
        '/usr/bin/env -i',
        '/bin/bash --noprofile --norc -p',
        'PATH=/usr/bin:/bin',
        'GIT_CONFIG_NOSYSTEM=1',
        'GIT_CONFIG_GLOBAL=/dev/null',
        'GIT_ATTR_NOSYSTEM=1',
        'GIT_NO_REPLACE_OBJECTS=1',
        'GIT_OPTIONAL_LOCKS=0',
        'GH_PROMPT_DISABLED=1',
        'trusted_git',
        'trusted_remote_git',
        'assert_git_local_config_safe',
        'assert_git_local_config_safe || return 1',
        'assert_git_transport_config_safe || return 1',
        '--local --no-includes --list',
        '/usr/bin/find -P .git -xdev',
        '.git/commondir',
        '.git/config.worktree',
        '.git/info/grafts',
        '.git/objects/info/alternates',
        '.git/objects/info/http-alternates',
        '-c core.alternateRefsCommand=/usr/bin/true',
        '-c core.attributesFile=/dev/null',
        '-c core.excludesFile=/dev/null',
        '-c core.fsmonitor=false',
        '-c core.hooksPath=/dev/null',
        '-c core.untrackedCache=false',
        '-c gc.auto=0',
        '-c gpg.format=openpgp',
        '-c gpg.program=/usr/bin/gpg',
        '-c gpg.openpgp.program=/usr/bin/gpg',
        '-c maintenance.auto=false',
        '-c push.followTags=false',
        '-c push.gpgSign=false',
        '-c push.pushOption=',
        '-c push.recurseSubmodules=no',
        '-c submodule.recurse=false',
        'git remote get-url --push origin',
        'xxvcc key 签名',
        'longlannet 发布',
        'git commit -S',
        'git verify-commit --raw',
        'git tag -s',
        'git verify-tag --raw',
        'refs/tags/$VERSION:refs/tags/$VERSION',
        'timeout-minutes',
        'PUBLICATION_LOCK_TARGET=',
        'PUBLICATION_LOCK=',
        'mkdir -m 700 -- "$PUBLICATION_LOCK_TARGET"',
        'unset SERPER_API_KEY SERPER_API_KEYS SERPER_DEBUG_RR TAR_OPTIONS',
        'release_tmp_directory_is_safe /tmp',
        "[ \"$(stat -c '%u:%a' -- \"$directory\")\" = '0:1777' ]",
        'git remote get-url origin',
        'trusted_git status --porcelain=v1 --untracked-files=all',
        'trusted_git diff --cached --check --no-ext-diff --no-textconv',
        'trusted_git diff --cached --name-status --no-ext-diff --no-textconv',
        '^/tmp/google-search-release\\.[A-Za-z0-9]{8}/google-search-$COMMIT\\.tar$',
        '[ "${AUDITED_ARCHIVE##*/}" = "google-search-${COMMIT}.tar" ]',
        '/tmp/google-search-release-canary.XXXXXXXX',
        '--install-dependencies',
        '--smoke-test --full-check --quiet',
        'repos/$REPOSITORY/immutable-releases',
        '--draft --verify-tag',
        'SHA256SUMS.asc',
        'gh release download',
        'cmp --',
        'sha256sum --check --strict',
        'gpg --batch --status-fd=1 --verify',
        '--draft=false --prerelease=false --latest --verify-tag',
        'isImmutable',
        'gh release verify "$VERSION"',
        'gh release verify-asset',
        'assert_release_identity',
        'assert_tag_name_unclaimed',
        'assert_release_name_unclaimed',
        'assert_publication_preconditions',
        '跨主机独占 publication window',
        '直到 immutable 终验完成',
        'draft notes/assets',
        'private_gnupg_home_is_safe',
        'stop_private_gpg_agent',
        '/usr/bin/gpgconf --homedir "$directory" --kill all',
        '/usr/bin/gpgconf --homedir "$directory" --remove-socketdir',
        '签名者当前权威渠道',
        '不得使用历史缓存、旧导出',
        '历史缓存或旧导出即使 fingerprint 相同也不能证明当前未撤销',
        "trusted_git config --get-regexp '^http\\.'",
        'trusted_git rev-parse --absolute-git-dir',
        'trusted_git rev-parse --path-format=absolute --git-common-dir',
        '[ "$(trusted_git symbolic-ref --quiet --short HEAD)" = main ]',
        'gh api --include "repos/$REPOSITORY/releases/tags/$VERSION"',
        '[ "$http_status" = 404 ]',
        '.author.login',
        "--template '{{.body}}'",
        'env -i PATH=/usr/bin:/bin /usr/bin/curl -q',
    )
    for fragment in required_fragments:
        assert fragment in runbook
    assert '--force' not in runbook
    assert '+refs/tags' not in runbook
    assert '-F enabled=true' not in runbook
    assert '! git show-ref' not in runbook
    assert 'local run_id= deadline=' not in runbook
    assert '--json body --jq .body' not in runbook
    assert 'gpg_status_is_acceptable' in runbook
    assert 'valid == 1 && forbidden == 0 && invalid == 0' in runbook
    assert '$10 != 8' in runbook
    assert '--digest-algo SHA256' in runbook
    assert 'GNUPGHOME="$FRESH_KEYRING" trusted_git verify-commit --raw' in runbook
    assert 'GNUPGHOME="$FRESH_KEYRING" trusted_git verify-tag --raw' in runbook
    assert 'verify_git_commit_signature "$COMMIT"' in runbook
    assert 'verify_git_tag_signature "$VERSION"' in runbook
    assert runbook.count('verify_checksum_signature "$') == 3
    assert 'IMPORTED_PRIMARY_FINGERPRINTS' in runbook
    assert '/tmp/google-search-release.????????' not in runbook
    assert 'if [ "$PUBLICATION_LOCK" = "$PUBLICATION_LOCK_TARGET" ]; then' in runbook
    assert '[ "$PUBLICATION_LOCK" = "$PUBLICATION_LOCK_TARGET" ] || exit 1' not in runbook
    local_guard = runbook.index('assert_git_local_config_safe() {')
    trusted_git = runbook.index('trusted_git() {')
    first_commit_resolution = runbook.index("COMMIT=\"$(trusted_git rev-parse")
    assert runbook.index('/usr/bin/env -i') < local_guard < trusted_git < first_commit_resolution
    assert 'ORIGINAL_GH_ACCOUNT=' in runbook
    assert 'GH_ACCOUNT_SWITCHED=1' in runbook
    assert runbook.index('GH_ACCOUNT_SWITCHED=1') < runbook.index(
        'gh auth switch --hostname github.com --user longlannet'
    )
    assert 'gh auth switch --hostname github.com --user "$ORIGINAL_GH_ACCOUNT"' in runbook
    lock_mkdir = 'mkdir -m 700 -- "$PUBLICATION_LOCK_TARGET"'
    lock_claim = 'PUBLICATION_LOCK="$PUBLICATION_LOCK_TARGET"'
    assert runbook.index('trap cleanup_release_state EXIT') < runbook.index(lock_mkdir)
    assert runbook.index("trap '' HUP INT TERM") < runbook.index(lock_mkdir)
    assert runbook.index(lock_mkdir) < runbook.index(lock_claim)
    assert runbook.index(lock_claim) < runbook.index("trap 'exit 129' HUP", runbook.index(lock_claim))
    assert '[ ! -e "$directory" ] && [ ! -L "$directory" ] || return 1' in runbook
    first_push = runbook.index(
        'trusted_remote_git push --atomic --no-follow-tags --signed=no --recurse-submodules=no'
    )
    fresh_key_import = runbook.index(
        'GNUPGHOME="$FRESH_KEYRING" /usr/bin/gpg --batch --import "$TRUSTED_PUBLIC_KEY"'
    )
    signed_commit = runbook.index(
        'trusted_git commit -S"$SIGNING_FINGERPRINT" -m "release: google-search ${VERSION}"'
    )
    assert fresh_key_import < signed_commit < runbook.index(
        'verify_git_commit_signature "$COMMIT"'
    ) < first_push
    first_tag_check = runbook.index('\nassert_tag_name_unclaimed\n')
    first_release_check = runbook.index('\nassert_release_name_unclaimed\n')
    account_switch = runbook.index('gh auth switch --hostname github.com --user longlannet')
    assert account_switch < first_tag_check < first_release_check < first_push
    assert runbook.count('\nassert_tag_name_unclaimed\n') == 2
    assert runbook.count('\nassert_release_name_unclaimed\n') == 4
    assert runbook.index(
        '\nassert_tag_name_unclaimed\nassert_release_name_unclaimed\ntrusted_git tag -s'
    ) > first_push
    publication_window = runbook.index('跨主机独占 publication window')
    second_tag_check = runbook.index('\nassert_tag_name_unclaimed\n', first_tag_check + 1)
    assert first_push < publication_window < second_tag_check
    assert runbook.index('AUDITED_ARCHIVE="${AUDITED_ARCHIVE:?') < first_push
    assert runbook.index('--smoke-test --full-check --quiet') < first_push
    assert runbook.index('gh auth switch') < first_push
    assert runbook.index("'.permissions.admin'") < first_push
    assert 'remote_tags="$(' in runbook
    assert ')" || return 1\n  [ -z "$remote_tags" ]' in runbook
    assert 'REMOTE_TAG_OBJECT=' in runbook
    assert '[ "$REMOTE_TAG_OBJECT" = "$LOCAL_TAG_OBJECT" ]' in runbook
    assert '[ "$REMOTE_TAG_COMMIT" = "$COMMIT" ]' in runbook
    assert '[ "$REMOTE_MAIN_BEFORE_TAG" = "$COMMIT" ]' in runbook
    assert runbook.count(
        'trusted_remote_git push --atomic --no-follow-tags --signed=no --recurse-submodules=no'
    ) == 2
    immutable_put = 'gh api --method PUT "repos/$REPOSITORY/immutable-releases" >/dev/null'
    draft_create = 'gh release create "$VERSION"'
    draft_download = 'gh release download "$VERSION"'
    publish = 'gh release edit "$VERSION"'
    assert runbook.index(immutable_put) < runbook.index(draft_create)
    assert runbook.index(
        'assert_release_name_unclaimed\ngh api --method PUT'
    ) < runbook.index(immutable_put)
    assert runbook.index(
        'assert_release_name_unclaimed\ngh release create'
    ) < runbook.index(draft_create)
    assert runbook.index('--draft --verify-tag') < runbook.index(draft_download)
    post_download = runbook[runbook.index(draft_download):]
    assert post_download.index('cmp -- "$STAGE/$ASSET" "$VERIFY/$ASSET"') < post_download.index(
        'sha256sum --check --strict SHA256SUMS'
    )
    assert post_download.index('sha256sum --check --strict SHA256SUMS') < post_download.index(
        'verify_checksum_signature "$VERIFY"'
    )
    precondition_calls = [
        match.start()
        for match in re.finditer(r'^assert_publication_preconditions$', runbook, re.MULTILINE)
    ]
    assert len(precondition_calls) == 4
    assert precondition_calls[0] < runbook.index(immutable_put)
    assert runbook.index(immutable_put) < precondition_calls[1] < runbook.index(draft_create)
    assert runbook.index(draft_download) < precondition_calls[2] < runbook.index(publish)
    assert runbook.index(publish) < precondition_calls[3]
    assert runbook.index(publish) < runbook.index('isDraft,isPrerelease,isImmutable')
    postpublish = runbook[runbook.index(publish):]
    assert 'assert_release_identity' in postpublish
    assert 'assert_publication_preconditions' in postpublish
    assert runbook.index('gh release verify-asset') < runbook.index(
        'env -i PATH=/usr/bin:/bin /usr/bin/curl -q'
    )
    assert runbook.index('cmp -- "$VERIFY/$name" "$PUBLIC/$name"') > runbook.index(publish)
    assert 'rmdir -- "$PUBLICATION_LOCK"' in runbook
    assert runbook.index('stop_private_gpg_agent "$FRESH_KEYRING"') < runbook.index(
        'for directory in "$PUBLIC" "$VERIFY" "$STAGE" "$FRESH_KEYRING"'
    )
    assert runbook.rindex('GH_ACCOUNT_SWITCHED=0') > runbook.index(
        'env -i PATH=/usr/bin:/bin /usr/bin/curl -q'
    )


def test_release_runbook_bash_blocks_are_syntax_and_shellcheck_clean():
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    blocks = re.findall(r'^```bash\n(.*?)^```$', runbook, flags=re.MULTILINE | re.DOTALL)
    assert blocks
    script = 'set -euo pipefail\n' + '\n'.join(blocks)
    syntax = subprocess.run(
        ['/bin/bash', '-n'],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    shellcheck = shutil.which('shellcheck')
    if shellcheck is None:
        pytest.skip('ShellCheck is not installed')
    lint = subprocess.run(
        [shellcheck, '--norc', '--shell=bash', '-'],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr


def test_release_launcher_clears_ambient_command_and_auth_injection(tmp_path):
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    launcher = re.findall(r'^```bash\n(.*?)^```$', runbook, flags=re.MULTILINE | re.DOTALL)[0]
    probe = (
        "/bin/bash --noprofile --norc -p -c '"
        'printf "git=%s\\n" "$(type -t git)"; /usr/bin/env'
        "'"
    )
    launcher = launcher.replace('/bin/bash --noprofile --norc -p', probe, 1)
    hostile_bash_env = tmp_path / 'hostile-bash-env'
    hostile_bash_env.write_text('exit 77\n', encoding='utf-8')
    environment = {
        **os.environ,
        'BASH_ENV': str(hostile_bash_env),
        'BASH_FUNC_git%%': '() { return 97; }',
        'LD_GOOGLE_SEARCH_SENTINEL': 'must-not-survive',
        'GIT_DIR': '/tmp/attacker-git-dir',
        'GIT_CONFIG_COUNT': '1',
        'GIT_CONFIG_GLOBAL': '/tmp/attacker-global-git-config',
        'GIT_CONFIG_KEY_0': 'core.hooksPath',
        'GIT_CONFIG_VALUE_0': '/tmp/attacker-hooks',
        'GH_TOKEN': 'must-not-survive',
        'GH_CONFIG_DIR': '/tmp/attacker-gh-config',
    }
    result = subprocess.run(
        ['/bin/bash', '--noprofile', '--norc', '-p', '-c', launcher],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == 'git=file'
    child_environment = {
        line.split('=', 1)[0]: line.split('=', 1)[1]
        for line in result.stdout.splitlines()[1:]
        if '=' in line
    }
    for name in (
        'BASH_ENV', 'BASH_FUNC_git%%', 'LD_GOOGLE_SEARCH_SENTINEL',
        'GIT_DIR', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_KEY_0',
        'GIT_CONFIG_VALUE_0', 'GH_TOKEN', 'GH_CONFIG_DIR',
    ):
        assert name not in child_environment
    assert child_environment['HOME'] == '/root'
    assert child_environment['PATH'] == '/usr/bin:/bin'
    assert child_environment['LC_ALL'] == 'C'


def _initialize_release_runbook_repository(repository, environment, remote_url):
    subprocess.run(
        ['/usr/bin/git', 'init', '-q', '-b', 'main'],
        cwd=repository,
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'remote', 'add', 'origin', str(remote_url)],
        cwd=repository,
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'config', '--local', 'branch.main.remote', 'origin'],
        cwd=repository,
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'config', '--local', 'branch.main.merge', 'refs/heads/main'],
        cwd=repository,
        env=environment,
        check=True,
    )


def test_release_tmp_guard_requires_canonical_root_owned_sticky_tmp(tmp_path):
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('release_tmp_directory_is_safe() {')
    end = runbook.index('\n}\nrelease_tmp_directory_is_safe /tmp', start) + 3
    helper = runbook[start:end]
    actual_tmp = subprocess.run(
        ['/bin/bash', '-c', f'{helper}\nrelease_tmp_directory_is_safe /tmp'],
        text=True,
        capture_output=True,
        check=False,
    )
    assert actual_tmp.returncode == 0, actual_tmp.stderr

    unsafe = tmp_path / 'non-sticky'
    unsafe.mkdir(mode=0o777)
    symlink = tmp_path / 'tmp-link'
    symlink.symlink_to('/tmp')
    for path in (unsafe, symlink, f'{tmp_path}/./non-sticky'):
        result = subprocess.run(
            ['/bin/bash', '-c', f'{helper}\nrelease_tmp_directory_is_safe "$1"', '_', str(path)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0, path


@pytest.mark.parametrize(
    'mutation',
    (
        'commondir',
        'config-worktree',
        'grafts',
        'alternates',
        'http-alternates',
        'refs-symlink',
        'head-hardlink',
        'writable-objects',
        'foreign-head',
    ),
)
def test_release_trusted_git_rejects_redirected_or_unsafe_metadata(tmp_path, mutation):
    if mutation == 'foreign-head' and os.geteuid() != 0:
        pytest.skip('foreign-owned Git metadata requires root')

    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('assert_git_local_config_safe() {')
    end = runbook.index('\n}\ntrusted_remote_git()', start) + 3
    helper = runbook[start:end]
    repository = tmp_path / 'repository'
    repository.mkdir()
    remote_url = 'https://github.com/longlannet/google-search.git'
    environment = {
        **os.environ,
        'HOME': str(tmp_path / 'home'),
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'REMOTE_URL': remote_url,
    }
    _initialize_release_runbook_repository(repository, environment, remote_url)
    command = f'{helper}\ntrusted_git rev-parse --git-common-dir >/dev/null\n'
    clean = subprocess.run(
        ['/bin/bash', '-c', command],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert clean.returncode == 0, clean.stderr

    git_dir = repository / '.git'
    if mutation == 'commondir':
        external_worktree = tmp_path / 'external-worktree'
        external_worktree.mkdir()
        _initialize_release_runbook_repository(external_worktree, environment, remote_url)
        (git_dir / 'commondir').write_text(
            f'{external_worktree / ".git"}\n',
            encoding='utf-8',
        )
        redirected = subprocess.run(
            ['/usr/bin/git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
            cwd=repository,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        assert redirected.stdout.strip() == str(external_worktree / '.git')
        effective_config = subprocess.run(
            ['/usr/bin/git', 'config', '--local', '--no-includes', '--list'],
            cwd=repository,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        expected_config = subprocess.run(
            ['/usr/bin/git', 'config', '--local', '--no-includes', '--list'],
            cwd=external_worktree,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        assert effective_config.stdout == expected_config.stdout
    elif mutation == 'config-worktree':
        (git_dir / 'config.worktree').write_text('[core]\n\tbare = false\n', encoding='utf-8')
    elif mutation == 'grafts':
        identity_environment = {
            **environment,
            'GIT_AUTHOR_NAME': 'Release Test',
            'GIT_AUTHOR_EMAIL': 'release@example.invalid',
            'GIT_COMMITTER_NAME': 'Release Test',
            'GIT_COMMITTER_EMAIL': 'release@example.invalid',
        }
        empty_tree = subprocess.run(
            ['/usr/bin/git', 'mktree'],
            cwd=repository,
            env=identity_environment,
            input='',
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        unrelated_commits = []
        for message in ('unrelated-a', 'unrelated-c'):
            unrelated_commits.append(subprocess.run(
                ['/usr/bin/git', 'commit-tree', empty_tree],
                cwd=repository,
                env=identity_environment,
                input=f'{message}\n',
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip())
        ancestor, descendant = unrelated_commits
        before_graft = subprocess.run(
            ['/usr/bin/git', '--no-replace-objects', 'merge-base', '--is-ancestor',
             ancestor, descendant],
            cwd=repository,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert before_graft.returncode == 1
        (git_dir / 'info' / 'grafts').write_text(
            f'{descendant} {ancestor}\n',
            encoding='ascii',
        )
        grafted = subprocess.run(
            ['/usr/bin/git', '--no-replace-objects', 'merge-base', '--is-ancestor',
             ancestor, descendant],
            cwd=repository,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert grafted.returncode == 0, grafted.stderr
    elif mutation == 'alternates':
        (git_dir / 'objects' / 'info' / 'alternates').write_text(
            f'{tmp_path / "external-objects"}\n',
            encoding='utf-8',
        )
    elif mutation == 'http-alternates':
        (git_dir / 'objects' / 'info' / 'http-alternates').write_text(
            'https://example.invalid/objects\n',
            encoding='utf-8',
        )
    elif mutation == 'refs-symlink':
        target = tmp_path / 'external-ref'
        target.write_text('0' * 40 + '\n', encoding='ascii')
        (git_dir / 'refs' / 'tags' / 'redirected').symlink_to(target)
    elif mutation == 'head-hardlink':
        external_head = tmp_path / 'external-head'
        external_head.write_text((git_dir / 'HEAD').read_text(encoding='ascii'), encoding='ascii')
        (git_dir / 'HEAD').unlink()
        os.link(external_head, git_dir / 'HEAD')
    elif mutation == 'writable-objects':
        (git_dir / 'objects').chmod(0o775)
    else:
        foreign_uid = next(uid for uid in (65534, 65533, 12345) if uid != os.geteuid())
        os.chown(git_dir / 'HEAD', foreign_uid, -1)

    protected = subprocess.run(
        ['/bin/bash', '-c', command],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert protected.returncode != 0


def test_release_trusted_git_disables_repository_hooks_and_fsmonitor(tmp_path):
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('assert_git_local_config_safe() {')
    end = runbook.index('\n}\ntrusted_remote_git()', start) + 3
    helper = runbook[start:end]
    repository = tmp_path / 'repository'
    fsmonitor = tmp_path / 'hostile-fsmonitor'
    hook_marker = tmp_path / 'hook-ran'
    fsmonitor_marker = tmp_path / 'fsmonitor-ran'
    repository.mkdir()
    environment = {
        **os.environ,
        'HOME': str(tmp_path / 'home'),
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'REMOTE_URL': 'https://github.com/longlannet/google-search.git',
        'RELEASE_TEST_HOOK_MARKER': str(hook_marker),
        'RELEASE_TEST_FSMONITOR_MARKER': str(fsmonitor_marker),
    }
    _initialize_release_runbook_repository(
        repository,
        environment,
        environment['REMOTE_URL'],
    )
    (repository / 'tracked').write_text('release candidate\n', encoding='utf-8')
    subprocess.run(['/usr/bin/git', 'add', 'tracked'], cwd=repository, env=environment, check=True)
    pre_commit = repository / '.git' / 'hooks' / 'pre-commit'
    pre_commit.write_text(
        '#!/bin/sh\n/usr/bin/touch -- "$RELEASE_TEST_HOOK_MARKER"\nexit 97\n',
        encoding='utf-8',
    )
    fsmonitor.write_text(
        '#!/bin/sh\n/usr/bin/touch -- "$RELEASE_TEST_FSMONITOR_MARKER"\nexit 97\n',
        encoding='utf-8',
    )
    pre_commit.chmod(0o700)
    fsmonitor.chmod(0o700)
    clean_result = subprocess.run(
        [
            '/bin/bash', '-c',
            f'{helper}\n'
            'trusted_git status --porcelain=v1 --untracked-files=all >/dev/null\n'
            'trusted_git diff --cached --check --no-ext-diff --no-textconv\n'
            'trusted_git commit -m "release test" >/dev/null\n',
        ],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert clean_result.returncode == 0, clean_result.stderr
    assert not hook_marker.exists()

    (repository / 'tracked').write_text('changed candidate\n', encoding='utf-8')
    subprocess.run(['/usr/bin/git', 'add', 'tracked'], cwd=repository, env=environment, check=True)
    subprocess.run(
        ['/usr/bin/git', 'config', '--local', 'core.fsmonitor', str(fsmonitor)],
        cwd=repository,
        env=environment,
        check=True,
    )
    hostile_result = subprocess.run(
        ['/bin/bash', '-c', f'{helper}\ntrusted_git status --porcelain=v1 >/dev/null'],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert hostile_result.returncode != 0
    assert not fsmonitor_marker.exists()


def _release_gpg_status_helper():
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('gpg_status_is_acceptable() {')
    end = runbook.index('\n}\nverify_git_commit_signature()', start) + 3
    return runbook[start:end]


def _gpg_status_is_accepted(status, fingerprint):
    result = subprocess.run(
        [
            '/bin/bash', '-c',
            f'{_release_gpg_status_helper()}\n'
            'SIGNING_FINGERPRINT="$2"\n'
            'gpg_status_is_acceptable "$1"\n',
            '_', status, fingerprint,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _gpg_agent_pids_for_home(home):
    expected = os.fsencode(os.fspath(home))
    matches = set()
    for process in Path('/proc').glob('[0-9]*'):
        try:
            arguments = (process / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        for index, argument in enumerate(arguments[:-1]):
            if argument == b'--homedir' and arguments[index + 1] == expected:
                if arguments and PurePosixPath(os.fsdecode(arguments[0])).name == 'gpg-agent':
                    matches.add(int(process.name))
                break
    return matches


def _wait_for_gpg_agent_state(home, *, running):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pids = _gpg_agent_pids_for_home(home)
        if bool(pids) is running:
            return pids
        time.sleep(0.05)
    return _gpg_agent_pids_for_home(home)


def _cleanup_test_gpg_home(home):
    socket_directory = subprocess.run(
        ['/usr/bin/gpgconf', '--homedir', os.fspath(home), '--list-dirs', 'socketdir'],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    ).stdout.strip()
    subprocess.run(
        ['/usr/bin/gpgconf', '--homedir', os.fspath(home), '--kill', 'all'],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    subprocess.run(
        ['/usr/bin/gpgconf', '--homedir', os.fspath(home), '--remove-socketdir'],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if socket_directory and socket_directory != os.fspath(home):
        socket_path = Path(socket_directory)
        if socket_path.is_dir() and not any(socket_path.iterdir()):
            socket_path.rmdir()


@pytest.mark.skipif(
    not Path('/usr/bin/gpg').is_file() or not Path('/usr/bin/gpgconf').is_file(),
    reason='GnuPG is required',
)
def test_release_gpg_agent_cleanup_is_bounded_idempotent_and_home_scoped(tmp_path):
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('private_release_directory_path_is_valid() {')
    end = runbook.index('\n}\ncleanup_release_state()', start) + 3
    helpers = runbook[start:end]

    empty_home = Path(subprocess.run(
        ['/usr/bin/mktemp', '-d', '/tmp/google-search-release-gnupg.XXXXXXXX'],
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    ).stdout.strip())
    empty_home.chmod(0o700)
    try:
        empty_result = subprocess.run(
            [
                '/bin/bash', '-c',
                f'{helpers}\n'
                'stop_private_gpg_agent "$1"\n'
                'stop_private_gpg_agent "$1"\n'
                'remove_private_release_directory "$1"\n',
                '_', os.fspath(empty_home),
            ],
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        assert empty_result.returncode == 0, empty_result.stderr
        assert not empty_home.exists()
    finally:
        if empty_home.exists():
            _cleanup_test_gpg_home(empty_home)
            shutil.rmtree(empty_home)

    source_home = tmp_path / 'source-gnupg'
    source_home.mkdir(mode=0o700)
    public_key = tmp_path / 'public-key.asc'
    identity = 'Release Cleanup Test <release-cleanup@example.invalid>'
    common = [
        '/usr/bin/gpg', '--batch', '--homedir', os.fspath(source_home),
        '--pinentry-mode', 'loopback', '--passphrase', '',
    ]
    target_home = None
    try:
        subprocess.run(
            [*common, '--quick-generate-key', identity, 'ed25519', 'sign', '1d'],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        public_key.write_bytes(subprocess.run(
            [*common, '--armor', '--export', identity],
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout)
        source_pids = _wait_for_gpg_agent_state(source_home, running=True)
        assert source_pids

        target_home = Path(subprocess.run(
            ['/usr/bin/mktemp', '-d', '/tmp/google-search-release-gnupg.XXXXXXXX'],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout.strip())
        target_home.chmod(0o700)
        subprocess.run(
            ['/usr/bin/gpg', '--batch', '--homedir', os.fspath(target_home),
             '--import', os.fspath(public_key)],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        target_pids = _wait_for_gpg_agent_state(target_home, running=True)
        assert target_pids
        target_socket_directory = Path(subprocess.run(
            ['/usr/bin/gpgconf', '--homedir', os.fspath(target_home),
             '--list-dirs', 'socketdir'],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout.strip())

        result = subprocess.run(
            [
                '/bin/bash', '-c',
                f'{helpers}\n'
                'stop_private_gpg_agent "$1"\n'
                'stop_private_gpg_agent "$1"\n'
                'remove_private_release_directory "$1"\n',
                '_', os.fspath(target_home),
            ],
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert not target_home.exists()
        assert not target_socket_directory.exists()
        assert not _wait_for_gpg_agent_state(target_home, running=False)
        assert all(Path('/proc', str(pid)).exists() for pid in source_pids)
    finally:
        if target_home is not None and target_home.exists():
            _cleanup_test_gpg_home(target_home)
            shutil.rmtree(target_home)
        _cleanup_test_gpg_home(source_home)


def test_release_gpg_status_requires_one_sha256_validsig_from_expected_primary():
    primary = 'A' * 40
    signing = 'B' * 40
    valid = f'[GNUPG:] VALIDSIG {signing} 2026-08-20 1 0 4 0 22 8 00 {primary}'
    assert _gpg_status_is_accepted(valid, primary)
    assert not _gpg_status_is_accepted(valid.replace(' 22 8 00 ', ' 22 2 00 '), primary)
    assert not _gpg_status_is_accepted(f'{valid}\n{valid}', primary)
    assert not _gpg_status_is_accepted(valid, 'C' * 40)


@pytest.mark.parametrize(
    'forbidden_token',
    (
        'BADSIG', 'ERRSIG', 'EXPSIG', 'EXPKEYSIG', 'REVKEYSIG',
        'KEYEXPIRED', 'KEYREVOKED', 'SIGEXPIRED',
    ),
)
def test_release_gpg_status_rejects_expiry_revocation_and_error_tokens(forbidden_token):
    primary = 'A' * 40
    valid = f'[GNUPG:] VALIDSIG {"B" * 40} 2026-08-20 1 0 4 0 22 8 00 {primary}'
    status = f'[GNUPG:] {forbidden_token}\n{valid}'
    assert not _gpg_status_is_accepted(status, primary)


@pytest.mark.skipif(not Path('/usr/bin/gpg').is_file(), reason='GnuPG is required')
def test_release_gpg_status_rejects_expired_keys_and_sha1_dynamically(tmp_path):
    gnupg_home = tmp_path / 'gnupg'
    gnupg_home.mkdir(mode=0o700)
    payload = tmp_path / 'payload'
    payload.write_text('signed release payload\n', encoding='utf-8')
    identity = 'Expired Release Test <expired-release@example.invalid>'
    fake_creation_time = '20210101T000000'
    valid_verification_time = '20210101T010000'
    common = [
        '/usr/bin/gpg', '--batch', '--homedir', str(gnupg_home),
        '--pinentry-mode', 'loopback', '--passphrase', '',
    ]
    socket_directory = None
    try:
        subprocess.run(
            [
                *common, '--faked-system-time', fake_creation_time,
                '--quick-generate-key', identity, 'ed25519', 'sign', '1d',
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        key_listing = subprocess.run(
            [*common, '--with-colons', '--list-secret-keys', identity],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        fingerprint = next(
            line.split(':')[9]
            for line in key_listing.splitlines()
            if line.startswith('fpr:')
        )
        socket_directory = Path(subprocess.run(
            ['/usr/bin/gpgconf', '--homedir', str(gnupg_home), '--list-dirs', 'socketdir'],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout.strip())
        signatures = {}
        for digest in ('SHA256', 'SHA1'):
            signature = tmp_path / f'{digest.lower()}.sig'
            subprocess.run(
                [
                    *common, '--faked-system-time', fake_creation_time,
                    '--allow-weak-digest-algos', '--local-user', fingerprint,
                    '--digest-algo', digest, '--detach-sign', '--output', str(signature),
                    str(payload),
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            signatures[digest] = signature

        def verification_status(signature, *, verification_time=None):
            command = [*common]
            if verification_time is not None:
                command.extend(('--faked-system-time', verification_time))
            command.extend((
                '--allow-weak-digest-algos', '--status-fd=1', '--verify',
                str(signature), str(payload),
            ))
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            return result.stdout

        valid_sha256 = verification_status(
            signatures['SHA256'],
            verification_time=valid_verification_time,
        )
        weak_sha1 = verification_status(
            signatures['SHA1'],
            verification_time=valid_verification_time,
        )
        expired_sha256 = verification_status(signatures['SHA256'])
        assert _gpg_status_is_accepted(valid_sha256, fingerprint)
        assert not _gpg_status_is_accepted(weak_sha1, fingerprint)
        assert ' VALIDSIG ' in weak_sha1
        assert not _gpg_status_is_accepted(expired_sha256, fingerprint)
        assert ' KEYEXPIRED ' in expired_sha256 or ' EXPKEYSIG ' in expired_sha256
    finally:
        _cleanup_test_gpg_home(gnupg_home)
    if socket_directory is not None and socket_directory != gnupg_home:
        assert not socket_directory.exists()


@pytest.mark.skipif(
    not Path('/usr/bin/gpg').is_file() or not Path('/usr/bin/gpgconf').is_file(),
    reason='GnuPG is required',
)
def test_release_authoritative_keyring_rejects_revoked_key_accepted_by_stale_keyring(tmp_path):
    source_home = tmp_path / 'source'
    stale_home = tmp_path / 'stale'
    authoritative_home = tmp_path / 'authoritative'
    for home in (source_home, stale_home, authoritative_home):
        home.mkdir(mode=0o700)
    payload = tmp_path / 'payload'
    payload.write_text('revocation-sensitive release payload\n', encoding='utf-8')
    signature = tmp_path / 'payload.sig'
    stale_public_key = tmp_path / 'stale-public-key.asc'
    authoritative_public_key = tmp_path / 'authoritative-public-key.asc'
    revocation_certificate = tmp_path / 'revocation.asc'
    identity = 'Revoked Release Test <revoked-release@example.invalid>'
    common = [
        '/usr/bin/gpg', '--batch', '--homedir', os.fspath(source_home),
        '--pinentry-mode', 'loopback', '--passphrase', '',
    ]
    try:
        subprocess.run(
            [*common, '--quick-generate-key', identity, 'ed25519', 'sign', '0'],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        listing = subprocess.run(
            [*common, '--with-colons', '--list-secret-keys', identity],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout
        fingerprint = next(
            line.split(':')[9]
            for line in listing.splitlines()
            if line.startswith('fpr:')
        )
        stale_public_key.write_bytes(subprocess.run(
            [*common, '--armor', '--export', fingerprint],
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout)
        subprocess.run(
            [*common, '--local-user', fingerprint, '--digest-algo', 'SHA256',
             '--detach-sign', '--output', os.fspath(signature), os.fspath(payload)],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        subprocess.run(
            ['/usr/bin/gpg', '--batch', '--homedir', os.fspath(stale_home),
             '--import', os.fspath(stale_public_key)],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )

        generated_revocation = source_home / 'openpgp-revocs.d' / f'{fingerprint}.rev'
        revocation_text = generated_revocation.read_text(encoding='ascii')
        marker = ':-----BEGIN PGP PUBLIC KEY BLOCK-----'
        assert marker in revocation_text
        revocation_certificate.write_text(
            revocation_text[revocation_text.index(marker):].replace(marker, marker[1:], 1),
            encoding='ascii',
        )
        subprocess.run(
            [*common, '--import', os.fspath(revocation_certificate)],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        authoritative_public_key.write_bytes(subprocess.run(
            [*common, '--armor', '--export', fingerprint],
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout)
        subprocess.run(
            ['/usr/bin/gpg', '--batch', '--homedir', os.fspath(authoritative_home),
             '--import', os.fspath(authoritative_public_key)],
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )

        def verification_status(home):
            return subprocess.run(
                ['/usr/bin/gpg', '--batch', '--homedir', os.fspath(home),
                 '--status-fd=1', '--verify', os.fspath(signature), os.fspath(payload)],
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )

        stale = verification_status(stale_home)
        authoritative = verification_status(authoritative_home)
        assert stale.returncode == 0, stale.stderr
        assert _gpg_status_is_accepted(stale.stdout, fingerprint)
        assert ' VALIDSIG ' in authoritative.stdout
        assert ' REVKEYSIG ' in authoritative.stdout or ' KEYREVOKED ' in authoritative.stdout
        assert not _gpg_status_is_accepted(authoritative.stdout, fingerprint)
    finally:
        for home in (authoritative_home, stale_home, source_home):
            _cleanup_test_gpg_home(home)


@pytest.mark.parametrize(
    ('response', 'status', 'expected_success'),
    (
        ('HTTP/2.0 404 Not Found\ncontent-type: application/json', 1, True),
        ('HTTP/2.0 403 Forbidden\ncontent-type: application/json', 1, False),
        ('network connection failed', 1, False),
        ('HTTP/2.0 404 Not Found', 2, False),
        ('{"id": 123, "draft": true}', 0, False),
    ),
)
def test_release_absence_check_accepts_only_an_exact_api_404(
    response,
    status,
    expected_success,
):
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('assert_release_name_unclaimed() {')
    end = runbook.index('\n}\ngpg_status_is_acceptable()', start) + 3
    helper = runbook[start:end]
    script = (
        'gh() { printf "%s\\n" "$STUB_RESPONSE"; return "$STUB_STATUS"; }\n'
        'REPOSITORY=longlannet/google-search\nVERSION=v2.0.0\n'
        f'{helper}\nassert_release_name_unclaimed\n'
    )
    result = subprocess.run(
        ['/bin/bash', '-c', script],
        env={**os.environ, 'STUB_RESPONSE': response, 'STUB_STATUS': str(status)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) is expected_success


@pytest.mark.parametrize(
    ('config_key', 'config_value'),
    (
        ('http.extraHeader', 'Authorization: hostile'),
        ('http.https://github.com/.extraHeader', 'Authorization: scoped-hostile'),
        ('http.https://github.com/.sslVerify', 'false'),
        ('http.proxy', 'https://127.0.0.1:9'),
    ),
)
def test_release_transport_guard_rejects_every_effective_http_config(
    tmp_path,
    config_key,
    config_value,
):
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    trusted_start = runbook.index('assert_git_local_config_safe() {')
    trusted_end = runbook.index('\n}\ntrusted_remote_git()', trusted_start) + 3
    start = runbook.index('assert_git_transport_config_safe() {')
    end = runbook.index('\n}\nCOMMIT="$(trusted_git rev-parse', start) + 3
    helpers = runbook[trusted_start:trusted_end] + '\n' + runbook[start:end]
    repository = tmp_path / 'repository'
    repository.mkdir()
    environment = {
        **os.environ,
        'HOME': str(tmp_path / 'home'),
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'REMOTE_URL': 'https://github.com/longlannet/google-search.git',
    }
    _initialize_release_runbook_repository(
        repository,
        environment,
        environment['REMOTE_URL'],
    )
    command = f'{helpers}\nassert_git_transport_config_safe'
    clean_result = subprocess.run(
        ['/bin/bash', '-c', command],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert clean_result.returncode == 0, clean_result.stderr
    assert 'command not found' not in clean_result.stderr

    subprocess.run(
        ['/usr/bin/git', 'config', '--local', config_key, config_value],
        cwd=repository,
        env=environment,
        check=True,
    )

    result = subprocess.run(
        ['/bin/bash', '-c', command],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert 'command not found' not in result.stderr


def test_release_remote_git_never_follows_ambient_tags_or_push_options(tmp_path):
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('assert_git_local_config_safe() {')
    guard_start = runbook.index('assert_git_transport_config_safe() {', start)
    end = runbook.index('\n}\nCOMMIT="$(trusted_git rev-parse', guard_start) + 3
    helpers = runbook[start:end]
    assert helpers.count('-c protocol.https.allow=always') == 1
    helpers = helpers.replace(
        '-c protocol.https.allow=always',
        '-c protocol.file.allow=always',
    )
    repository = tmp_path / 'repository'
    control_remote = tmp_path / 'control.git'
    protected_remote = tmp_path / 'protected.git'
    repository.mkdir()
    environment = {
        **os.environ,
        'HOME': str(tmp_path / 'home'),
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'REMOTE_URL': str(protected_remote),
    }
    subprocess.run(
        ['/usr/bin/git', 'init', '--bare', '-q', str(control_remote)],
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'init', '--bare', '-q', str(protected_remote)],
        env=environment,
        check=True,
    )
    _initialize_release_runbook_repository(
        repository,
        environment,
        protected_remote,
    )
    (repository / 'tracked').write_text('release candidate\n', encoding='utf-8')
    subprocess.run(['/usr/bin/git', 'add', 'tracked'], cwd=repository, env=environment, check=True)
    identity = ['-c', 'user.name=Release Test', '-c', 'user.email=release@example.invalid']
    subprocess.run(
        ['/usr/bin/git', *identity, 'commit', '-q', '-m', 'release test'],
        cwd=repository,
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', *identity, 'tag', '-a', 'leaked-tag', '-m', 'must not follow'],
        cwd=repository,
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'config', '--local', 'push.followTags', 'true'],
        cwd=repository,
        env=environment,
        check=True,
    )
    commit = subprocess.run(
        ['/usr/bin/git', 'rev-parse', '--verify', 'HEAD^{commit}'],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    subprocess.run(
        ['/usr/bin/git', 'push', str(control_remote), f'{commit}:refs/heads/main'],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    control_tag = subprocess.run(
        ['/usr/bin/git', '--git-dir', str(control_remote), 'show-ref', '--verify',
         'refs/tags/leaked-tag'],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert control_tag.returncode == 0

    subprocess.run(
        ['/usr/bin/git', 'config', '--local', '--add', 'push.pushOption', 'hostile-option'],
        cwd=repository,
        env=environment,
        check=True,
    )
    command = (
        f'{helpers}\n'
        'trusted_remote_git push --atomic --no-follow-tags --signed=no --recurse-submodules=no '
        '"$REMOTE_URL" "$1:refs/heads/main"\n'
    )
    protected_push = subprocess.run(
        ['/bin/bash', '-c', command, '_', commit],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert protected_push.returncode != 0
    protected_tag = subprocess.run(
        ['/usr/bin/git', '--git-dir', str(protected_remote), 'for-each-ref',
         '--format=%(refname)', 'refs/tags/'],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    assert protected_tag.stdout == ''
    protected_refs = subprocess.run(
        ['/usr/bin/git', '--git-dir', str(protected_remote), 'for-each-ref',
         '--format=%(refname)'],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    assert protected_refs.stdout == ''


def test_release_atomic_tag_push_rejects_tag_when_main_already_advanced(tmp_path):
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('assert_git_local_config_safe() {')
    guard_start = runbook.index('assert_git_transport_config_safe() {', start)
    end = runbook.index('\n}\nCOMMIT="$(trusted_git rev-parse', guard_start) + 3
    helpers = runbook[start:end]
    assert helpers.count('-c protocol.https.allow=always') == 1
    helpers = helpers.replace(
        '-c protocol.https.allow=always',
        '-c protocol.file.allow=always',
    )
    repository = tmp_path / 'repository'
    competitor = tmp_path / 'competitor'
    remote = tmp_path / 'remote.git'
    repository.mkdir()
    environment = {
        **os.environ,
        'HOME': str(tmp_path / 'home'),
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'REMOTE_URL': str(remote),
    }
    subprocess.run(
        ['/usr/bin/git', 'init', '--bare', '-q', str(remote)],
        env=environment,
        check=True,
    )
    _initialize_release_runbook_repository(repository, environment, remote)
    identity = ['-c', 'user.name=Release Test', '-c', 'user.email=release@example.invalid']
    (repository / 'tracked').write_text('release candidate\n', encoding='utf-8')
    subprocess.run(['/usr/bin/git', 'add', 'tracked'], cwd=repository, env=environment, check=True)
    subprocess.run(
        ['/usr/bin/git', *identity, 'commit', '-q', '-m', 'release candidate'],
        cwd=repository,
        env=environment,
        check=True,
    )
    commit = subprocess.run(
        ['/usr/bin/git', 'rev-parse', '--verify', 'HEAD^{commit}'],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ['/usr/bin/git', 'push', str(remote), f'{commit}:refs/heads/main'],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', '--git-dir', str(remote), 'symbolic-ref', 'HEAD', 'refs/heads/main'],
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'clone', '-q', str(remote), str(competitor)],
        env=environment,
        check=True,
    )
    (competitor / 'tracked').write_text('concurrent update\n', encoding='utf-8')
    subprocess.run(['/usr/bin/git', 'add', 'tracked'], cwd=competitor, env=environment, check=True)
    subprocess.run(
        ['/usr/bin/git', *identity, 'commit', '-q', '-m', 'concurrent update'],
        cwd=competitor,
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'push', 'origin', 'HEAD:refs/heads/main'],
        cwd=competitor,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    advanced_commit = subprocess.run(
        ['/usr/bin/git', 'rev-parse', '--verify', 'HEAD^{commit}'],
        cwd=competitor,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ['/usr/bin/git', *identity, 'tag', '-a', 'v2.0.0', '-m', 'release'],
        cwd=repository,
        env=environment,
        check=True,
    )

    command = (
        f'{helpers}\n'
        'trusted_remote_git push --atomic --no-follow-tags --signed=no '
        '--recurse-submodules=no "$REMOTE_URL" "$1:refs/heads/main" '
        'refs/tags/v2.0.0:refs/tags/v2.0.0\n'
    )
    result = subprocess.run(
        ['/bin/bash', '-c', command, '_', commit],
        cwd=repository,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    remote_main = subprocess.run(
        ['/usr/bin/git', '--git-dir', str(remote), 'rev-parse', '--verify',
         'refs/heads/main^{commit}'],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert remote_main == advanced_commit
    remote_tags = subprocess.run(
        ['/usr/bin/git', '--git-dir', str(remote), 'for-each-ref',
         '--format=%(refname)', 'refs/tags/'],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    assert remote_tags.stdout == ''


def test_release_local_config_guard_blocks_alternate_refs_command(tmp_path):
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('assert_git_local_config_safe() {')
    guard_start = runbook.index('assert_git_transport_config_safe() {', start)
    end = runbook.index('\n}\nCOMMIT="$(trusted_git rev-parse', guard_start) + 3
    helpers = runbook[start:end]
    assert helpers.count('-c protocol.https.allow=always') == 1
    helpers = helpers.replace(
        '-c protocol.https.allow=always',
        '-c protocol.file.allow=always',
    )
    source = tmp_path / 'source'
    remote = tmp_path / 'remote.git'
    checkout = tmp_path / 'checkout'
    marker = tmp_path / 'alternate-command-ran'
    command_script = tmp_path / 'alternate-command'
    source.mkdir()
    environment = {
        **os.environ,
        'HOME': str(tmp_path / 'home'),
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'REMOTE_URL': str(remote),
        'RELEASE_TEST_ALTERNATE_MARKER': str(marker),
    }
    subprocess.run(
        ['/usr/bin/git', 'init', '-q', '-b', 'main'],
        cwd=source,
        env=environment,
        check=True,
    )
    identity = ['-c', 'user.name=Release Test', '-c', 'user.email=release@example.invalid']
    (source / 'tracked').write_text('first\n', encoding='utf-8')
    subprocess.run(['/usr/bin/git', 'add', 'tracked'], cwd=source, env=environment, check=True)
    subprocess.run(
        ['/usr/bin/git', *identity, 'commit', '-q', '-m', 'first'],
        cwd=source,
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'init', '--bare', '-q', str(remote)],
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'push', str(remote), 'HEAD:refs/heads/main'],
        cwd=source,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', '--git-dir', str(remote), 'symbolic-ref', 'HEAD', 'refs/heads/main'],
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'clone', '--shared', '-q', str(remote), str(checkout)],
        env=environment,
        check=True,
    )
    (source / 'tracked').write_text('second\n', encoding='utf-8')
    subprocess.run(['/usr/bin/git', 'add', 'tracked'], cwd=source, env=environment, check=True)
    subprocess.run(
        ['/usr/bin/git', *identity, 'commit', '-q', '-m', 'second'],
        cwd=source,
        env=environment,
        check=True,
    )
    subprocess.run(
        ['/usr/bin/git', 'push', str(remote), 'HEAD:refs/heads/main'],
        cwd=source,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    command_script.write_text(
        '#!/bin/sh\n/usr/bin/touch -- "$RELEASE_TEST_ALTERNATE_MARKER"\n',
        encoding='utf-8',
    )
    command_script.chmod(0o700)
    subprocess.run(
        ['/usr/bin/git', 'config', '--local', 'core.alternateRefsCommand', str(command_script)],
        cwd=checkout,
        env=environment,
        check=True,
    )

    subprocess.run(
        ['/usr/bin/git', 'fetch', str(remote),
         'refs/heads/main:refs/remotes/origin/main'],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    assert marker.exists()
    marker.unlink()

    command = (
        f'{helpers}\n'
        'trusted_remote_git fetch --no-tags --no-prune --recurse-submodules=no '
        '--no-write-fetch-head --no-write-commit-graph --no-auto-maintenance '
        '"$REMOTE_URL" refs/heads/main:refs/remotes/origin/main\n'
    )
    protected_fetch = subprocess.run(
        ['/bin/bash', '-c', command],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert protected_fetch.returncode != 0
    assert not marker.exists()


@pytest.mark.parametrize(
    ('local_status', 'remote_status', 'remote_output', 'expected_success'),
    (
        (1, 0, '', True),
        (0, 0, '', False),
        (128, 0, '', False),
        (1, 2, '', False),
        (1, 0, 'deadbeef\trefs/tags/v2.0.0', False),
    ),
)
def test_release_tag_absence_check_fails_closed(
    local_status,
    remote_status,
    remote_output,
    expected_success,
):
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('assert_tag_name_unclaimed() {')
    end = runbook.index('\n}\nassert_release_name_unclaimed()', start) + 3
    helper = runbook[start:end]
    script = (
        'trusted_git() { return "$STUB_LOCAL_STATUS"; }\n'
        'trusted_remote_git() { printf "%s" "$STUB_REMOTE_OUTPUT"; '
        'return "$STUB_REMOTE_STATUS"; }\n'
        'REMOTE_URL=https://github.com/longlannet/google-search.git\nVERSION=v2.0.0\n'
        f'{helper}\nassert_tag_name_unclaimed\n'
    )
    result = subprocess.run(
        ['/bin/bash', '-c', script],
        env={
            **os.environ,
            'STUB_LOCAL_STATUS': str(local_status),
            'STUB_REMOTE_STATUS': str(remote_status),
            'STUB_REMOTE_OUTPUT': remote_output,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) is expected_success


def test_release_cleanup_path_guard_rejects_traversal_and_noncanonical_paths():
    runbook = (ROOT / 'references/releasing.md').read_text(encoding='utf-8')
    start = runbook.index('private_release_directory_path_is_valid() {')
    end = runbook.index('\n}\nremove_private_release_directory()', start) + 3
    guard = runbook[start:end]
    probes = (
        '/tmp/google-search-release./../..///',
        '/tmp/google-search-release.ABCDEFG/',
        '/tmp/google-search-release-verify.ABC/../DEFG',
        '/tmp/google-search-release-canary.ABCDEFGH/child',
        '/tmp/google-search-release-canary.ABCDEFG!',
    )
    for probe in probes:
        result = subprocess.run(
            ['/bin/bash', '-c', guard + '\nprivate_release_directory_path_is_valid "$1"', '_', probe],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0, probe

    for allowed in (
        '/tmp/google-search-release.A1b2C3d4',
        '/tmp/google-search-release-assets.A1b2C3d4',
        '/tmp/google-search-release-verify.A1b2C3d4',
        '/tmp/google-search-release-public.A1b2C3d4',
        '/tmp/google-search-release-gnupg.A1b2C3d4',
        '/tmp/google-search-release-canary.A1b2C3d4',
    ):
        result = subprocess.run(
            ['/bin/bash', '-c', guard + '\nprivate_release_directory_path_is_valid "$1"', '_', allowed],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, allowed


def test_supported_entrypoint_calls_use_privileged_bash():
    paths = [
        ROOT / '.github' / 'workflows' / 'test.yml',
        ROOT / 'CHANGELOG.md',
        ROOT / 'README.md',
        ROOT / 'SKILL.md',
        ROOT / 'config' / 'serper.env.example',
        ROOT / 'scripts' / 'helptext.py',
        ROOT / 'scripts' / 'install.sh',
        ROOT / 'scripts' / 'run.sh',
        ROOT / 'scripts' / 'check.sh',
        *(ROOT / 'references').glob('*.md'),
    ]
    unsafe_fragments = (
        'bash scripts/install.sh',
        'bash scripts/run.sh',
        'bash scripts/check.sh',
        'bash "{baseDir}/scripts/install.sh',
        'bash "{baseDir}/scripts/run.sh',
        'bash "{baseDir}/scripts/check.sh',
        'bash "$RUNNER"',
        'bash -n',
    )
    for path in paths:
        text = path.read_text(encoding='utf-8')
        for fragment in unsafe_fragments:
            assert fragment not in text, (path, fragment)


def test_runtime_and_development_locks_match_inputs_and_each_other():
    runtime_pins, runtime_includes = _parse_input('requirements.in')
    development_pins, development_includes = _parse_input('requirements-dev.in')
    runtime = _parse_lock('requirements.txt')
    development = _parse_lock('requirements-dev.txt')

    assert runtime_includes == []
    assert development_includes == ['requirements.in']
    assert runtime_pins == {'requests': '2.34.2'}
    assert development_pins == {'pytest': '9.1.1'}
    assert {name for name, _ in runtime} == RUNTIME_PACKAGE_NAMES
    assert set(runtime) < set(development)
    for key, value in runtime.items():
        assert development[key] == value
    for name, version in {**runtime_pins, **development_pins}.items():
        matching_versions = {entry_version for (entry_name, _), (entry_version, _) in development.items() if entry_name == name}
        assert matching_versions == {version}


@pytest.mark.parametrize(
    ('lock_name', 'input_name', 'label'),
    [
        ('requirements.txt', 'requirements.in', 'runtime'),
        ('requirements-dev.txt', 'requirements-dev.in', 'development'),
    ],
)
def test_lock_recipe_pins_uv_and_resolution_cutoff(lock_name, input_name, label):
    recipe_label = f'uv {UV_VERSION}; cutoff={LOCK_CUTOFF}; {label} lock (see README.md)'
    header = (ROOT / lock_name).read_text(encoding='utf-8').splitlines()[:2]
    assert header == [
        '# This file was autogenerated by uv via the following command:',
        f'#    {recipe_label}',
    ]

    readme = (ROOT / 'README.md').read_text(encoding='utf-8').replace('\\\n', ' ')
    normalized_readme = ' '.join(readme.split())
    assert 'UV_BIN="$(command -v uv)"' in readme
    assert 'set -eu' in readme
    assert 'env -i UV_CUSTOM_COMPILE_COMMAND="uv ' in normalized_readme
    for fixed_option in (
        '"$UV_BIN" --no-config --no-cache pip compile',
        '--default-index=https://pypi.org/simple',
        '--index-strategy=first-index',
        '--keyring-provider=disabled',
        '--resolution=highest',
        '--prerelease=if-necessary-or-explicit',
        '--fork-strategy=requires-python',
        '--no-sources',
        '--no-python-downloads',
        '--only-binary=:all:',
        '--upgrade',
        '--universal',
        '--python-version=3.10',
        '--generate-hashes',
        f'--exclude-newer={LOCK_CUTOFF}',
    ):
        assert fixed_option in normalized_readme
    assert f'compile_lock {label} {input_name} {lock_name}' in normalized_readme
