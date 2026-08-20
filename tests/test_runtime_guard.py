import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / 'scripts' / 'runtime_guard.py'
PACKAGES = {
    'certifi': ('2026.7.22', 'certifi'),
    'charset-normalizer': ('3.5.1', 'charset_normalizer'),
    'idna': ('3.19', 'idna'),
    'requests': ('2.34.2', 'requests'),
    'urllib3': ('2.7.0', 'urllib3'),
}
MODULES = {
    'certifi': "def where():\n    return __file__\n",
    'charset_normalizer': "def from_bytes(value):\n    return value\n",
    'idna': (
        "def encode(value):\n    return str(value).encode('ascii')\n"
        "def decode(value):\n    return bytes(value).decode('ascii')\n"
    ),
    'requests': (
        'class RequestException(Exception):\n    pass\n'
        'class Timeout(RequestException):\n    pass\n'
        'class Session:\n'
        '    def post(self, *args, **kwargs):\n        return None\n'
        '    def close(self):\n        pass\n'
    ),
    'urllib3': 'class PoolManager:\n    pass\n',
}


def write_fake_system_site(root):
    root.mkdir()
    for distribution, (version, module_name) in PACKAGES.items():
        package = root / module_name
        package.mkdir()
        module_file = package / '__init__.py'
        module_file.write_text(MODULES[module_name], encoding='utf-8')
        metadata = root / f'{module_name}-{version}.dist-info'
        metadata.mkdir()
        metadata_file = metadata / 'METADATA'
        metadata_file.write_text(
            f'Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n',
            encoding='utf-8',
        )
        record = metadata / 'RECORD'
        record.write_text(
            f'{module_name}/__init__.py,,\n'
            f'{metadata.name}/METADATA,,\n'
            f'{metadata.name}/RECORD,,\n',
            encoding='utf-8',
        )
    return root


def run_guard_harness(site_root, body, *, env=None, guard_path=GUARD):
    source = (
        'import importlib.util, os\n'
        f'spec = importlib.util.spec_from_file_location("runtime_guard", {str(guard_path)!r})\n'
        'guard = importlib.util.module_from_spec(spec)\n'
        'spec.loader.exec_module(guard)\n'
        f'site_root = {str(site_root)!r}\n'
        f'{body}\n'
    )
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, '-I', '-S', '-B', '-c', source],
        check=False,
        capture_output=True,
        text=True,
        env=merged_env,
    )


def test_guard_ignores_safe_malicious_startup_hooks_and_preserves_key(tmp_path):
    site_root = write_fake_system_site(tmp_path / 'site-packages')
    marker = tmp_path / 'startup-hook-ran'
    payload = (
        'import os, pathlib; '
        f'pathlib.Path({str(marker)!r}).write_text(os.environ.get("SERPER_API_KEY", "missing"))'
    )
    (site_root / 'malicious.pth').write_text(f'{payload}\n', encoding='utf-8')
    (site_root / 'sitecustomize.py').write_text(f'{payload}\n', encoding='utf-8')

    result = run_guard_harness(
        site_root,
        'guard.validate_and_activate([site_root]); print(os.environ["SERPER_API_KEY"])',
        env={'SERPER_API_KEY': 'guard-test-secret'},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'guard-test-secret'
    assert not marker.exists()


@pytest.mark.parametrize('hook_name', ('malicious.pth', 'sitecustomize.py'))
def test_guard_rejects_writable_startup_hook_without_executing_it(tmp_path, hook_name):
    site_root = write_fake_system_site(tmp_path / 'site-packages')
    marker = tmp_path / 'writable-hook-ran'
    hook = site_root / hook_name
    hook.write_text(
        f'import pathlib; pathlib.Path({str(marker)!r}).write_text("ran")\n',
        encoding='utf-8',
    )
    hook.chmod(0o666)

    result = run_guard_harness(
        site_root,
        'guard.validate_and_activate([site_root])',
        env={'SERPER_API_KEYS': 'guard-test-secret-one,guard-test-secret-two'},
    )

    assert result.returncode != 0
    assert not marker.exists()


def test_guard_rejects_writable_locked_package_file(tmp_path):
    site_root = write_fake_system_site(tmp_path / 'site-packages')
    (site_root / 'requests' / '__init__.py').chmod(0o666)

    result = run_guard_harness(site_root, 'guard.validate_and_activate([site_root])')

    assert result.returncode != 0


def test_guard_rejects_locked_module_origin_not_claimed_by_distribution(tmp_path):
    site_root = write_fake_system_site(tmp_path / 'site-packages')
    record = site_root / 'requests-2.34.2.dist-info' / 'RECORD'
    record.write_text(
        'requests-2.34.2.dist-info/METADATA,,\n'
        'requests-2.34.2.dist-info/RECORD,,\n',
        encoding='utf-8',
    )

    result = run_guard_harness(site_root, 'guard.validate_and_activate([site_root])')

    assert result.returncode != 0


def test_guard_ignores_manifest_entries_outside_site_roots(tmp_path):
    site_root = write_fake_system_site(tmp_path / 'site-packages')
    record = site_root / 'idna-3.19.dist-info' / 'RECORD'
    record.write_text(
        record.read_text(encoding='utf-8') + '../../../bin/idna,,\n',
        encoding='utf-8',
    )

    result = run_guard_harness(site_root, 'guard.validate_and_activate([site_root])')

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ('module_path', 'import_statement', 'consumer_name'),
    (
        ('chardet', 'import chardet', 'requests'),
        ('simplejson', 'import simplejson', 'requests'),
        ('socks', 'import socks', 'requests'),
        ('brotli', 'import brotli', 'urllib3'),
        ('brotlicffi', 'import brotlicffi', 'urllib3'),
        ('backports/zstd', 'from backports import zstd', 'urllib3'),
    ),
)
def test_guard_blocks_unlocked_optional_module_before_import(
    tmp_path,
    module_path,
    import_statement,
    consumer_name,
):
    site_root = write_fake_system_site(tmp_path / 'site-packages')
    marker = tmp_path / f'{module_path.replace("/", "-")}-imported'
    optional_module = site_root.joinpath(*module_path.split('/'))
    optional_module.mkdir(parents=True)
    (optional_module / '__init__.py').write_text(
        f'import pathlib\npathlib.Path({str(marker)!r}).write_text("imported")\n'
        '__version__ = "7.0.0"\n',
        encoding='utf-8',
    )
    consumer = site_root / consumer_name / '__init__.py'
    consumer.write_text(
        f'{import_statement}\n' + consumer.read_text(encoding='utf-8'),
        encoding='utf-8',
    )

    result = run_guard_harness(site_root, 'guard.validate_and_activate([site_root])')

    assert result.returncode != 0
    assert not marker.exists()


def test_guard_blocks_unclaimed_locked_package_module_before_import(tmp_path):
    site_root = write_fake_system_site(tmp_path / 'site-packages')
    marker = tmp_path / 'unclaimed-module-imported'
    unclaimed_module = site_root / 'requests' / 'unclaimed.py'
    unclaimed_module.write_text(
        f'import pathlib\npathlib.Path({str(marker)!r}).write_text("imported")\n',
        encoding='utf-8',
    )
    requests_init = site_root / 'requests' / '__init__.py'
    requests_init.write_text(
        'from . import unclaimed\n' + requests_init.read_text(encoding='utf-8'),
        encoding='utf-8',
    )

    result = run_guard_harness(site_root, 'guard.validate_and_activate([site_root])')

    assert result.returncode != 0
    assert not marker.exists()


def test_guard_keeps_blocking_unlocked_modules_after_activation(tmp_path):
    site_root = write_fake_system_site(tmp_path / 'site-packages')
    marker = tmp_path / 'late-module-imported'
    optional_module = site_root / 'late_optional.py'
    optional_module.write_text(
        f'import pathlib\npathlib.Path({str(marker)!r}).write_text("imported")\n',
        encoding='utf-8',
    )

    result = run_guard_harness(
        site_root,
        'guard.validate_and_activate([site_root]); import late_optional',
    )

    assert result.returncode != 0
    assert not marker.exists()


def test_guard_target_script_cannot_shadow_the_standard_library(tmp_path):
    site_root = write_fake_system_site(tmp_path / 'site-packages')
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    guard_path = scripts / 'runtime_guard.py'
    shutil.copy2(GUARD, guard_path)
    marker = tmp_path / 'shadow-json-imported'
    (scripts / 'json.py').write_text(
        f'import pathlib\npathlib.Path({str(marker)!r}).write_text("imported")\n'
        'raise SystemExit(97)\n',
        encoding='utf-8',
    )
    target = scripts / 'target.py'
    target.write_text(
        'import json\nprint(json.dumps({"stdlib": True}, sort_keys=True))\n',
        encoding='utf-8',
    )

    result = run_guard_harness(
        site_root,
        f'guard.validate_and_activate([site_root]); guard._run_script({str(target)!r}, [])',
        guard_path=guard_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '{"stdlib": true}'
    assert not marker.exists()


def test_guard_cli_requires_isolated_no_site_startup():
    result = subprocess.run(
        [sys.executable, '-I', '-B', str(GUARD), 'probe', 'test-sentinel'],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout == ''
