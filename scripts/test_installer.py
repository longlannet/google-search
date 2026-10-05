"""Offline installer transactions with real renames, locks and shell signals.

Only the copied installer's venv builder and mv/rm commands are substituted.
The builder creates a real Python venv with local package stubs, so these tests
exercise validation and transaction behavior without downloading dependencies.
They do not establish pip/network or real dependency compatibility.
"""

import fcntl
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent
BUILDER = r'''#!/usr/bin/python3
import os
from pathlib import Path
import re
import subprocess
import sys

if sys.argv[1:4] != ['-I', '-m', 'venv']:
    os.execv('/usr/bin/python3', ['/usr/bin/python3', *sys.argv[1:]])
runtime = Path(sys.argv[4])
subprocess.run(['/usr/bin/python3', '-I', '-m', 'venv', '--without-pip', str(runtime)], check=True)
site = runtime / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
root = Path(os.environ['INSTALLER_TEST_ROOT'])
for package, version in re.findall(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)', (root / 'requirements.txt').read_text(), re.M):
    metadata = site / f'{package.replace("-", "_")}-{version}.dist-info'
    metadata.mkdir()
    (metadata / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {package}\nVersion: {version}\n')
    if package == 'requests':
        requests = site / 'requests'
        requests.mkdir()
        (requests / '__init__.py').write_text(
            f'__version__ = {version!r}\n'
            'import os\nfrom pathlib import Path\n'
            'runtime = Path(__file__).parents[4]\n'
            'if os.environ.get("INSTALLER_TEST_FAIL_VALIDATION") and runtime.name == ".venv" and (runtime / "identity").read_text() == "new":\n'
            '    raise RuntimeError("injected post-publication validation failure")\n'
        )
pip = site / 'pip'
pip.mkdir()
(pip / '__init__.py').write_text('')
(pip / '__main__.py').write_text('import sys\nraise SystemExit(0)\n')
(runtime / 'identity').write_text('new')
'''

COMMAND = r'''#!/usr/bin/python3
import os
from pathlib import Path
import signal
import subprocess
import sys

command = Path(sys.argv[0]).name
root = Path(os.environ['INSTALLER_TEST_ROOT'])
args = sys.argv[1:]
event = ''
if command == 'mv':
    source, target = map(Path, args[-2:])
    if source == root / '.venv' and target.name.startswith('.venv-previous.'):
        event = 'backup-move'
    elif source.name.startswith('.venv-build.') and target == root / '.venv':
        event = 'publish'
elif command == 'rm' and Path(args[-1]).name.startswith('.venv-previous.'):
    event = 'backup-remove'

def inject(phase):
    if event and os.environ.get('INSTALLER_TEST_SIGNAL_POINT') == f'{event}-{phase}':
        (root / 'signal-delivered').write_text(f'{event}-{phase}')
        os.kill(os.getppid(), int(os.environ['INSTALLER_TEST_SIGNAL']))
        raise SystemExit(0)

inject('before')
if event and os.environ.get('INSTALLER_TEST_FAIL_COMMAND') == event:
    raise SystemExit(73)
status = subprocess.run(['/usr/bin/' + command, *args]).returncode
if status == 0:
    inject('after')
raise SystemExit(status)
'''


class InstallerFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.scripts = self.root / 'scripts'
        self.scripts.mkdir(mode=0o700)
        for source in SCRIPT_DIR.iterdir():
            if source.suffix in {'.py', '.sh'} and source.is_file():
                shutil.copyfile(source, self.scripts / source.name)
        shutil.copyfile(SCRIPT_DIR.parent / 'requirements.txt', self.root / 'requirements.txt')
        self.tools = self.root / 'test-tools'
        self.tools.mkdir(mode=0o700)
        for name, content in [('python', BUILDER), ('mv', COMMAND), ('rm', COMMAND)]:
            target = self.tools / name
            target.write_text(content, encoding='ascii')
            target.chmod(0o700)
        self.environment = {
            'PATH': '/usr/bin:/bin',
            'HOME': str(self.root),
            'INSTALLER_TEST_ROOT': str(self.root),
        }
        installer = self.scripts / 'install.sh'
        source = installer.read_text(encoding='utf-8')
        source = source.replace(
            'for candidate in /usr/bin/python3 /usr/local/bin/python3; do',
            f'for candidate in {shlex.quote(str(self.tools / "python"))}; do',
        )
        for command in ('mv', 'rm'):
            source = source.replace(
                '/usr/bin/' + command + ' ', shlex.quote(str(self.tools / command)) + ' ',
            )
        installer.write_text(source, encoding='utf-8')

    @property
    def runtime(self):
        return self.root / '.venv'

    @property
    def lock(self):
        return self.root / '.venv.install.lock'

    def seed_runtime(self):
        subprocess.run(
            [str(self.tools / 'python'), '-I', '-m', 'venv', str(self.runtime)],
            env=self.environment, check=True, capture_output=True, timeout=15,
        )
        (self.runtime / 'identity').write_text('old', encoding='ascii')

    def seed_lock(self, mode=0o600):
        self.lock.write_text('existing lock\n', encoding='ascii')
        self.lock.chmod(mode)

    def run(self, *args, **environment):
        return subprocess.run(
            ['/bin/bash', '-p', str(self.scripts / 'install.sh'), *args],
            env={**self.environment, **environment}, text=True,
            capture_output=True, timeout=20,
        )

    def identity(self):
        return (self.runtime / 'identity').read_text(encoding='ascii')

    def leftovers(self):
        return list(self.root.glob('.venv-build.*')) + list(self.root.glob('.venv-previous.*'))

    def snapshot(self):
        result = {}
        for path in [self.root, *self.root.rglob('*')]:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                content = os.readlink(path)
            elif stat.S_ISREG(metadata.st_mode):
                content = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                content = None
            result[str(path.relative_to(self.root))] = (
                metadata.st_mode, metadata.st_uid, metadata.st_gid,
                metadata.st_ino, metadata.st_nlink, metadata.st_mtime_ns,
                metadata.st_ctime_ns, content,
            )
        return result


class InstallerTransactionTests(unittest.TestCase):
    def make_fixture(self, existing=True):
        temporary = tempfile.TemporaryDirectory(prefix='google-search-installer-')
        self.addCleanup(temporary.cleanup)
        fixture = InstallerFixture(temporary.name)
        if existing:
            fixture.seed_runtime()
        return fixture

    def test_success_installs_and_releases_lock(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                fixture = self.make_fixture(existing)
                result = fixture.run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fixture.identity(), 'new')
                self.assertEqual(fixture.leftovers(), [])
                with fixture.lock.open('rb') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_signals_before_commit_restore_old_runtime(self):
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            for point in ('backup-move-after', 'publish-before', 'publish-after'):
                with self.subTest(signal=signum, point=point):
                    fixture = self.make_fixture()
                    result = fixture.run(
                        INSTALLER_TEST_SIGNAL_POINT=point,
                        INSTALLER_TEST_SIGNAL=str(int(signum)),
                    )
                    self.assertEqual(result.returncode, 128 + signum, result.stderr)
                    self.assertEqual((fixture.root / 'signal-delivered').read_text(), point)
                    self.assertEqual(fixture.identity(), 'old')
                    self.assertEqual(fixture.leftovers(), [])

    def test_signals_during_backup_removal_preserve_committed_runtime(self):
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            for phase in ('before', 'after'):
                with self.subTest(signal=signum, phase=phase):
                    fixture = self.make_fixture()
                    point = f'backup-remove-{phase}'
                    result = fixture.run(
                        INSTALLER_TEST_SIGNAL_POINT=point,
                        INSTALLER_TEST_SIGNAL=str(int(signum)),
                    )
                    self.assertEqual(result.returncode, 128 + signum, result.stderr)
                    self.assertEqual((fixture.root / 'signal-delivered').read_text(), point)
                    self.assertEqual(fixture.identity(), 'new')
                    if phase == 'after':
                        self.assertEqual(fixture.leftovers(), [])
                    else:
                        self.assertEqual(len(fixture.leftovers()), 1)
                        self.assertIn(str(fixture.leftovers()[0]), result.stderr)

    def test_fresh_install_interrupted_after_publish_removes_uncommitted_runtime(self):
        fixture = self.make_fixture(existing=False)
        result = fixture.run(
            INSTALLER_TEST_SIGNAL_POINT='publish-after',
            INSTALLER_TEST_SIGNAL=str(int(signal.SIGTERM)),
        )
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertFalse(fixture.runtime.exists())
        self.assertEqual(fixture.leftovers(), [])

    def test_failed_backup_or_publication_keeps_old_runtime(self):
        for command, status in (('backup-move', 73), ('publish', 3)):
            with self.subTest(command=command):
                fixture = self.make_fixture()
                result = fixture.run(INSTALLER_TEST_FAIL_COMMAND=command)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(fixture.identity(), 'old')
                self.assertEqual(fixture.leftovers(), [])

    def test_failed_validation_restores_old_runtime(self):
        fixture = self.make_fixture()
        result = fixture.run(INSTALLER_TEST_FAIL_VALIDATION='1')
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(fixture.identity(), 'old')
        self.assertEqual(fixture.leftovers(), [])

    def test_failed_backup_removal_preserves_new_runtime_and_reports_backup(self):
        fixture = self.make_fixture()
        result = fixture.run(INSTALLER_TEST_FAIL_COMMAND='backup-remove')
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertEqual(fixture.identity(), 'new')
        self.assertEqual(len(fixture.leftovers()), 1)
        self.assertIn(str(fixture.leftovers()[0]), result.stderr)

    def test_check_is_read_only_for_healthy_runtime(self):
        fixture = self.make_fixture()
        fixture.seed_lock()
        before = fixture.snapshot()
        result = fixture.run('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fixture.snapshot(), before)

    def test_check_is_read_only_for_missing_or_unsafe_lock(self):
        for mode in (None, 0o644, 0o666):
            with self.subTest(mode=mode):
                fixture = self.make_fixture()
                if mode is not None:
                    fixture.seed_lock(mode)
                before = fixture.snapshot()
                result = fixture.run('--check')
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertEqual(fixture.snapshot(), before)

    def test_check_is_read_only_for_missing_runtime(self):
        fixture = self.make_fixture(existing=False)
        fixture.seed_lock()
        before = fixture.snapshot()
        result = fixture.run('--check')
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(fixture.snapshot(), before)

    def test_check_is_read_only_when_lock_is_busy(self):
        fixture = self.make_fixture()
        fixture.seed_lock()
        with fixture.lock.open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = fixture.snapshot()
            result = fixture.run('--check')
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn('busy', result.stderr)
            self.assertEqual(fixture.snapshot(), before)

    def test_check_allows_concurrent_readers(self):
        fixture = self.make_fixture()
        fixture.seed_lock()
        with fixture.lock.open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            result = fixture.run('--check')
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
