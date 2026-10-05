import fcntl
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parent


class ShellSyntaxGateTests(unittest.TestCase):
    def assert_syntax_rejected(self, script_name):
        with tempfile.TemporaryDirectory(prefix='google-search-shell-test-') as temporary:
            root = Path(temporary)
            scripts = root / 'scripts'
            scripts.mkdir(mode=0o700)
            for name in ('run.sh', 'install.sh', 'check.sh'):
                shutil.copyfile(SCRIPT_DIR / name, scripts / name)
            broken_script = scripts / script_name
            with broken_script.open('a', encoding='utf-8') as output:
                output.write('\nif then\n')

            shellcheck_marker = root / 'shellcheck-ran'
            shellcheck = root / 'shellcheck'
            shellcheck.write_text(
                '#!/bin/bash\n'
                'touch -- "${0%/*}/shellcheck-ran"\n'
                'exit 73\n',
                encoding='ascii',
            )
            shellcheck.chmod(0o700)
            result = subprocess.run(
                ['/bin/bash', '-p', str(scripts / 'check.sh'),
                 '--shellcheck', str(shellcheck)],
                env={'PATH': '/usr/bin:/bin', 'HOME': temporary, 'LC_ALL': 'C'},
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('[google-search] checking shell syntax', result.stdout)
            self.assertIn(str(broken_script), result.stderr)
            self.assertIn('syntax error', result.stderr)
            self.assertFalse(shellcheck_marker.exists(), 'ShellCheck masked the Bash syntax check')

    def test_run_script_syntax_error_is_rejected(self):
        self.assert_syntax_rejected('run.sh')

    def test_installer_syntax_error_is_rejected(self):
        self.assert_syntax_rejected('install.sh')

    def test_check_script_syntax_error_is_rejected(self):
        self.assert_syntax_rejected('check.sh')


class RuntimeLockGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='google-search-lock-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scripts = self.root / 'scripts'
        self.scripts.mkdir(mode=0o700)
        for name in ('run.sh', 'install.sh', 'check.sh'):
            shutil.copyfile(SCRIPT_DIR / name, self.scripts / name)
        self.guard_marker = self.scripts / 'guard-ran'
        (self.scripts / 'runtime_guard.py').write_text(
            'from pathlib import Path\n'
            'Path(__file__).with_name("guard-ran").touch()\n'
            'raise SystemExit(73)\n',
            encoding='ascii',
        )
        self.lock = self.root / '.venv.install.lock'
        self.lock.touch(mode=0o600)
        self.python_marker = self.root / '.venv' / 'bin' / 'python-ran'

    def add_runtime(self):
        python = self.root / '.venv' / 'bin' / 'python'
        python.parent.mkdir(parents=True)
        python.write_text(
            '#!/bin/bash\n'
            'touch -- "${0%/*}/python-ran"\n'
            'exit 74\n',
            encoding='ascii',
        )
        python.chmod(0o700)

    def assert_rejected_before_guard_or_python(self, message):
        result = subprocess.run(
            ['/bin/bash', '-p', str(self.scripts / 'check.sh')],
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root), 'LC_ALL': 'C'},
            cwd=self.root, capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.guard_marker.exists(), 'Runtime guard ran before the lock was acquired')
        self.assertFalse(self.python_marker.exists(), 'Virtualenv Python ran before the lock was acquired')

    def test_busy_runtime_is_rejected_before_execution(self):
        self.add_runtime()
        with self.lock.open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_rejected_before_guard_or_python('runtime installation is busy')

    def test_busy_installation_with_no_runtime_cannot_fall_back(self):
        with self.lock.open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_rejected_before_guard_or_python('runtime installation is busy')

    def test_existing_lock_with_missing_runtime_cannot_fall_back(self):
        self.assert_rejected_before_guard_or_python('required .venv runtime not found')

    def test_missing_lock_is_not_created(self):
        self.add_runtime()
        self.lock.unlink()
        self.assert_rejected_before_guard_or_python('install lock missing or unsafe')
        self.assertFalse(self.lock.exists())

    def test_unsafe_lock_is_not_repaired(self):
        self.add_runtime()
        self.lock.chmod(0o644)
        before = self.lock.stat()
        self.assert_rejected_before_guard_or_python('install lock metadata is unsafe')
        after = self.lock.stat()
        self.assertEqual((after.st_mode, after.st_ctime_ns), (before.st_mode, before.st_ctime_ns))


if __name__ == '__main__':
    unittest.main(verbosity=2)
