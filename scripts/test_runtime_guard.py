import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent
ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'}


class RuntimeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='google-runtime-test-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scripts = self.root / 'scripts'
        self.scripts.mkdir()
        for name in ('run.sh', 'runtime_guard.py'):
            shutil.copyfile(SCRIPTS / name, self.scripts / name)
        self.entry = self.scripts / 'search.py'
        self.entry.write_text('print("TRUSTED_ENTRY")\n', encoding='ascii')
        self.runtime = self.root / '.venv'
        subprocess.run(
            [str(Path(sys._base_executable).resolve(strict=True)), '-I', '-B', '-m', 'venv', '--without-pip', str(self.runtime)],
            env=ENVIRONMENT, check=True, capture_output=True, timeout=30,
        )
        self.site = next((self.runtime / 'lib').glob('python*/site-packages'))
        (self.root / '.venv.install.lock').touch(mode=0o600)

    def run_wrapper(self, expected=3):
        result = subprocess.run(
            ['/bin/bash', '-p', str(self.scripts / 'run.sh')],
            env=ENVIRONMENT, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        if expected:
            self.assertNotIn('TRUSTED_ENTRY', result.stdout)
            self.assertNotIn('ATTACKER_ENTRY', result.stdout)
        return result

    def test_safe_default_venv_runs_without_writing_bytecode(self):
        module = self.scripts / 'helper.py'
        module.write_text('value = "TRUSTED_ENTRY"\n', encoding='ascii')
        self.entry.write_text('from helper import value\nprint(value)\n', encoding='ascii')
        self.assertIn('TRUSTED_ENTRY', self.run_wrapper(0).stdout)
        self.assertFalse((self.scripts / '__pycache__').exists())

    def test_standard_pi_interpreter_alias_resolves_to_base(self):
        alias = self.runtime / 'bin' / '\U0001d70bthon'
        if not alias.is_symlink():
            alias.symlink_to('python')
        self.assertIn('TRUSTED_ENTRY', self.run_wrapper(0).stdout)

    def test_rejects_pi_alias_pointing_to_other_executable(self):
        alias = self.runtime / 'bin' / '\U0001d70bthon'
        alias.unlink(missing_ok=True)
        attacker = self.root / 'other-interpreter'
        attacker.write_text('#!/bin/sh\nprintf ATTACKER_ENTRY\n', encoding='ascii')
        attacker.chmod(0o755)
        alias.symlink_to(attacker)
        self.run_wrapper()

    def test_rejects_unrecognized_bin_alias_even_to_base(self):
        (self.runtime / 'bin' / 'other-python').symlink_to('python')
        self.run_wrapper()

    def test_rejects_writable_entry_import_and_cached_bytecode(self):
        for name in ('search.py', 'helper.py', '__pycache__/helper.pyc'):
            with self.subTest(name=name):
                target = self.scripts / name
                target.parent.mkdir(exist_ok=True)
                if target != self.entry:
                    target.write_bytes(b'not executed')
                target.chmod(0o666)
                self.run_wrapper()
                target.chmod(0o644)

    def test_rejects_writable_package_and_intermediate_directory(self):
        module = self.site / 'unsafe_dependency.py'
        module.write_text('raise RuntimeError("must not import")\n', encoding='ascii')
        module.chmod(0o666)
        self.run_wrapper()
        module.chmod(0o644)
        (self.runtime / 'lib').chmod(0o777)
        self.run_wrapper()

    def test_rejects_unsafe_guard_before_loading_it(self):
        guard = self.scripts / 'runtime_guard.py'
        guard.write_text('print("ATTACKER_ENTRY")\n', encoding='ascii')
        guard.chmod(0o666)
        self.run_wrapper()

    def test_rejects_writable_interpreter_before_version_probe(self):
        target = self.root / 'fake-python'
        target.write_text('#!/bin/sh\nprintf ATTACKER_ENTRY\n', encoding='ascii')
        target.chmod(0o777)
        python = self.runtime / 'bin' / 'python'
        python.unlink()
        python.symlink_to(target)
        self.run_wrapper()

    def test_rejects_symlink_import_and_pth_override(self):
        external = self.root / 'outside.py'
        external.write_text('print("ATTACKER_ENTRY")\n', encoding='ascii')
        linked = self.site / 'external.py'
        linked.symlink_to(external)
        self.run_wrapper()
        linked.unlink()
        (self.runtime / 'bin' / 'python._pth').write_text('import site\n', encoding='ascii')
        self.run_wrapper()

    def test_rejects_symlink_parent_traversal_before_executing_interpreter(self):
        safe = self.root / 'safe'
        unsafe = self.root / 'unsafe'
        (safe / 'evil').mkdir(parents=True)
        (unsafe / 'child').mkdir(parents=True)
        (unsafe / 'evil').mkdir()
        (safe / 'alias').symlink_to(unsafe / 'child', target_is_directory=True)
        (safe / 'evil' / 'python3').symlink_to(sys._base_executable)
        target = unsafe / 'evil' / 'python3'
        target.write_text('#!/bin/sh\nprintf ATTACKER_ENTRY\n', encoding='ascii')
        target.chmod(0o777)
        (unsafe / 'evil').chmod(0o777)
        ambiguous_home = safe / 'alias' / '..' / 'evil'
        (self.runtime / 'pyvenv.cfg').write_text(f'home = {ambiguous_home}\n', encoding='ascii')
        for python in (self.runtime / 'bin').glob('python*'):
            python.unlink()
            python.symlink_to(ambiguous_home / 'python3')
        self.run_wrapper()

    def test_rejects_writable_ancestor(self):
        self.root.chmod(0o777)
        self.run_wrapper()

    @unittest.skipUnless(os.geteuid() == 0 and shutil.which('setpriv'), 'requires root and setpriv')
    def test_other_uid_cannot_supply_code_to_root_runner(self):
        self.root.chmod(0o755)
        self.scripts.chmod(0o755)
        self.entry.chmod(0o666)
        result = subprocess.run(
            [
                '/usr/bin/setpriv', '--reuid=65534', '--regid=65534', '--clear-groups',
                '/usr/bin/python3', '-I', '-S', '-B', '-c',
                'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("print(\\\"ATTACKER_ENTRY\\\")\\n")',
                str(self.entry),
            ],
            env=ENVIRONMENT, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ATTACKER_ENTRY', self.entry.read_text(encoding='ascii'))
        self.run_wrapper()


if __name__ == '__main__':
    unittest.main(verbosity=2)
