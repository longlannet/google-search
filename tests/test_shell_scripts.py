import fcntl
import json
import os
import pwd
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGES = {
    'certifi': '2026.7.22',
    'charset-normalizer': '3.5.1',
    'idna': '3.19',
    'requests': '2.34.2',
    'urllib3': '2.7.0',
}
DEVELOPMENT_PACKAGES = {
    'iniconfig': '2.3.0',
    'packaging': '26.3',
    'pluggy': '1.6.0',
    'pygments': '2.21.0',
}
PYTHON_310_DEVELOPMENT_PACKAGES = {
    'exceptiongroup': '1.3.1',
    'tomli': '2.4.1',
    'typing-extensions': '4.16.0',
}
CHECK_INTEGRATION_TIMEOUT = 120
INSTALL_ONLINE_TIMEOUT = 120


def run_command(args, *, cwd, env=None, timeout=30, pass_fds=()):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        args,
        cwd=cwd,
        env=merged_env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        pass_fds=pass_fds,
    )


def make_test_repo(tmp_path):
    repo = tmp_path / 'google-search'
    scripts = repo / 'scripts'
    config = repo / 'config'
    tests = repo / 'tests'
    scripts.mkdir(parents=True)
    config.mkdir()
    tests.mkdir()

    for name in (
        'install.sh',
        'check.sh',
        'run.sh',
        'secure_io.py',
        'client.py',
        'args.py',
        'io_common.py',
        'check_protocol.py',
        'venv_transaction.py',
        'locked_install.py',
        'runtime_guard.py',
    ):
        shutil.copy2(ROOT / 'scripts' / name, scripts / name)
    for name in ('requirements.txt', 'requirements-dev.txt'):
        shutil.copy2(ROOT / name, repo / name)

    (repo / 'SKILL.md').write_text('test skill\n', encoding='utf-8')
    (repo / 'README.md').write_text('test readme\n', encoding='utf-8')
    (scripts / 'search.py').write_text(
        'import json, sys\nprint(json.dumps(sys.argv[1:]))\n',
        encoding='utf-8',
    )
    (scripts / 'smoke_test.py').write_text(
        'import json, os, pathlib\n'
        'marker = os.environ.get("NETWORK_MARKER")\n'
        'if marker:\n'
        '    pathlib.Path(marker).write_text("smoke")\n'
        'path_record = os.environ.get("CHECK_RESULT_PATH_RECORD")\n'
        'if path_record:\n'
        '    pathlib.Path(path_record).write_text(os.readlink("/proc/self/fd/1"))\n'
        'bad_mode = os.environ.get("CHECK_BAD_MODE") if os.environ.get("CHECK_BAD_TARGET") == "smoke" else None\n'
        'if bad_mode == "empty":\n'
        '    raise SystemExit(0)\n'
        'if bad_mode == "object":\n'
        '    print("{}")\n'
        'elif bad_mode == "schema":\n'
        '    print(json.dumps({"ok": True, "trust": "untrusted_external_content", "kind": "wrong"}))\n'
        'else:\n'
        '    print(json.dumps({\n'
        '        "ok": True, "trust": "untrusted_external_content", "kind": "smoke-test",\n'
        '        "endpoint": "search", "query": "OpenClaw", "keyCount": 1, "keySlot": 1,\n'
        '        "organicCount": 1, "shape": {"hasOrganic": True, "topLevelKeys": ["organic"],\n'
        '        "listLengths": {"organic": 1}},\n'
        '    }))\n',
        encoding='utf-8',
    )
    (scripts / 'selfcheck.py').write_text(
        'import json, os, pathlib, sys\n'
        'from check_protocol import (FULL_ENDPOINTS, FULL_GROUPS, NEGATIVE_ENDPOINTS,\n'
        '    NEGATIVE_ENDPOINT_MESSAGES, NETWORK_ENDPOINT_QUERIES, NETWORK_LIST_KEYS)\n'
        'def shape(list_name=None, count=1, scalar_keys=()):\n'
        '    keys = list(scalar_keys)\n'
        '    lengths = {}\n'
        '    if list_name is not None:\n'
        '        keys.append(list_name)\n'
        '        lengths[list_name] = count\n'
        '    keys = sorted(set(keys))\n'
        '    return {"topLevelKeys": keys, "listLengths": lengths,\n'
        '        "hasOrganic": lengths.get("organic", 0) > 0, "hasAnswerBox": False,\n'
        '        "hasKnowledgeGraph": False, "hasCredits": "credits" in keys,\n'
        '        "hasSearchParameters": False, "hasNonEmptyText": "text" in keys}\n'
        'def endpoint_result(name):\n'
        '    if name in NEGATIVE_ENDPOINTS:\n'
        '        return {"ok": True, "expectedError": "UsageError",\n'
        '            "message": NEGATIVE_ENDPOINT_MESSAGES[name]}\n'
        '    if name == "maps-reviews-all":\n'
        '        return {"ok": True, "query": "coffee shanghai", "resultCount": 3,\n'
        '            "failedCount": 0, "allSucceeded": True, "mapsShape": shape("places", 3),\n'
        '            "reviewShapes": [shape("reviews", 0) for _ in range(3)],\n'
        '            "error": None}\n'
        '    if name in {"maps-reviews", "maps-reviews-pick2"}:\n'
        '        return {"ok": True, "query": "coffee shanghai",\n'
        '            "pick": 2 if name.endswith("pick2") else 1,\n'
        '            "selectedPlace": {"placeId": "test-place"},\n'
        '            "mapsShape": shape("places", 3), "reviewsShape": shape("reviews"),\n'
        '            "error": None}\n'
        '    if name == "webpage":\n'
        '        response_shape = shape(scalar_keys=("text",))\n'
        '    elif name == "lens":\n'
        '        response_shape = shape("visualMatches")\n'
        '    else:\n'
        '        response_shape = shape(NETWORK_LIST_KEYS[name][0])\n'
        '    return {"ok": True, "query": NETWORK_ENDPOINT_QUERIES[name],\n'
        '        "keySlot": 1, "shape": response_shape}\n'
        'record = os.environ.get("SELFCHECK_RECORD")\n'
        'if record:\n'
        '    pathlib.Path(record).write_text(json.dumps(sys.argv[1:]))\n'
        'target = "parsing" if "--group" in sys.argv else "full"\n'
        'if target == "full":\n'
        '    marker = os.environ.get("NETWORK_MARKER")\n'
        '    if marker:\n'
        '        pathlib.Path(marker).write_text("full")\n'
        'path_record = os.environ.get("CHECK_RESULT_PATH_RECORD")\n'
        'if path_record:\n'
        '    pathlib.Path(path_record).write_text(os.readlink("/proc/self/fd/1"))\n'
        'bad_mode = os.environ.get("CHECK_BAD_MODE") if os.environ.get("CHECK_BAD_TARGET") == target else None\n'
        'if bad_mode == "empty":\n'
        '    raise SystemExit(0)\n'
        'if bad_mode == "object":\n'
        '    print("{}")\n'
        'elif bad_mode == "schema":\n'
        '    print(json.dumps({"ok": True, "trust": "untrusted_external_content", "kind": "selfcheck",\n'
        '        "mode": target, "exitCode": 0, "selectedGroups": [target],\n'
        '        "keyCount": 1, "endpointsTested": ["single"], "results": {"single": {"ok": True}},\n'
        '        "errors": [], "failureKinds": []}))\n'
        'else:\n'
        '    endpoints = list(NEGATIVE_ENDPOINTS if target == "parsing" else FULL_ENDPOINTS)\n'
        '    print(json.dumps({\n'
        '        "ok": True, "trust": "untrusted_external_content", "kind": "selfcheck",\n'
        '        "mode": "group" if target == "parsing" else "full", "exitCode": 0,\n'
        '        "keyCount": 0 if target == "parsing" else 1,\n'
        '        "selectedGroups": ["parsing"] if target == "parsing" else FULL_GROUPS,\n'
        '        "endpointsTested": endpoints, "results": {name: endpoint_result(name) for name in endpoints},\n'
        '        "errors": [], "failureKinds": [],\n'
        '    }))\n',
        encoding='utf-8',
    )
    (tests / 'test_placeholder.py').write_text('def test_ok():\n    assert True\n', encoding='utf-8')
    return repo


def create_stub_runtime(repo, *, with_pytest=False):
    venv = repo / '.venv'
    subprocess.run(
        [sys.executable, '-m', 'venv', '--without-pip', str(venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    python = venv / 'bin' / 'python'
    site_packages = Path(
        subprocess.run(
            [str(python), '-I', '-c', 'import site; print(site.getsitepackages()[0])'],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    modules = {
        'certifi': (
            "__version__ = '2026.7.22'\n"
            "def where():\n    return __file__\n"
        ),
        'charset_normalizer': (
            "__version__ = '3.5.1'\n"
            "def from_bytes(value):\n    return value\n"
        ),
        'idna': (
            "__version__ = '3.19'\n"
            "def encode(value):\n    return str(value).encode('ascii')\n"
            "def decode(value):\n    return bytes(value).decode('ascii')\n"
        ),
        'requests': (
            "__version__ = '2.34.2'\n"
            "class RequestException(Exception):\n    pass\n"
            "class Timeout(RequestException):\n    pass\n"
            "class Session:\n"
            "    def __init__(self):\n        self.trust_env = True\n"
            "    def post(self, *args, **kwargs):\n        raise AssertionError('network disabled in shell test')\n"
            "    def close(self):\n        pass\n"
        ),
        'urllib3': (
            "__version__ = '2.7.0'\n"
            "class PoolManager:\n    pass\n"
        ),
    }
    for distribution, version in RUNTIME_PACKAGES.items():
        module_name = distribution.replace('-', '_')
        package = site_packages / module_name
        package.mkdir()
        (package / '__init__.py').write_text(modules[module_name], encoding='utf-8')
        dist_info = site_packages / f'{module_name}-{version}.dist-info'
        dist_info.mkdir()
        (dist_info / 'METADATA').write_text(
            f'Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n',
            encoding='utf-8',
        )

    if with_pytest:
        development_packages = dict(DEVELOPMENT_PACKAGES)
        if sys.version_info[:2] < (3, 11):
            development_packages.update(PYTHON_310_DEVELOPMENT_PACKAGES)
        for distribution, version in development_packages.items():
            dist_info_name = distribution.replace('-', '_')
            dist_info = site_packages / f'{dist_info_name}-{version}.dist-info'
            dist_info.mkdir()
            (dist_info / 'METADATA').write_text(
                f'Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n',
                encoding='utf-8',
            )

        pytest_files = {
            'pytest/__init__.py': (
                'from enum import IntEnum\n'
                'class ExitCode(IntEnum):\n    OK = 0\n    TESTS_FAILED = 1\n'
                'from _pytest.config import Config, main\n'
                'from _pytest.main import Session\n'
                'from _pytest.nodes import Item\n'
                "__version__ = '9.1.1'\n"
            ),
            'pytest/__main__.py': (
                'import os, pathlib\n'
                'marker = os.environ.get("CHECK_PYTEST_MAIN_MARKER")\n'
                'if marker:\n    pathlib.Path(marker).write_text("forged __main__ executed")\n'
                'raise SystemExit(97)\n'
            ),
            '_pytest/__init__.py': '',
            '_pytest/config/__init__.py': (
                'import json, os, pathlib\n'
                'from types import SimpleNamespace\n'
                'class Config:\n    pass\n'
                'def main(args=None, plugins=None):\n'
                '    record = os.environ.get("CHECK_PYTEST_ENV_RECORD")\n'
                '    if record:\n'
                '        pathlib.Path(record).write_text(json.dumps({\n'
                '            "addopts": os.environ.get("PYTEST_ADDOPTS"),\n'
                '            "plugins": os.environ.get("PYTEST_PLUGINS"),\n'
                '            "debug_temproot": os.environ.get("PYTEST_DEBUG_TEMPROOT"),\n'
                '            "autoload": os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"),\n'
                '            "SERPER_API_KEY": os.environ.get("SERPER_API_KEY"),\n'
                '            "SERPER_API_KEYS": os.environ.get("SERPER_API_KEYS"),\n'
                '            "release_archive": os.environ.get("GOOGLE_SEARCH_RELEASE_ARCHIVE"),\n'
                '            "release_commit": os.environ.get("GOOGLE_SEARCH_RELEASE_COMMIT"),\n'
                '            "path": os.environ.get("PATH"), "tmpdir": os.environ.get("TMPDIR"),\n'
                '            "tmp": os.environ.get("TMP"), "temp": os.environ.get("TEMP"),\n'
                '            "args": args,\n'
                '        }))\n'
                '    mutation = os.environ.get("CHECK_PYTEST_SOURCE_MUTATION")\n'
                '    if mutation:\n'
                '        target = pathlib.Path(os.environ["CHECK_PYTEST_SOURCE_MUTATION_TARGET"])\n'
                '        if mutation == "add":\n'
                '            target.write_text("added during pytest\\n", encoding="utf-8")\n'
                '        elif mutation == "replace":\n'
                '            replacement = target.with_name(target.name + ".pytest-replacement")\n'
                '            replacement.write_text("replaced during pytest\\n", encoding="utf-8")\n'
                '            os.replace(replacement, target)\n'
                '        else:\n'
                '            raise AssertionError("unknown source mutation mode")\n'
                '    tests_dir = pathlib.Path(args[0]).resolve()\n'
                '    items = [SimpleNamespace(path=path.resolve(), nodeid=f"{path.name}::test_stub")\n'
                '             for path in sorted(tests_dir.rglob("test_*.py"))]\n'
                '    session = SimpleNamespace(items=items)\n'
                '    for plugin in plugins or []:\n        plugin.pytest_collection_finish(session)\n'
                '    for item in items:\n'
                '        for plugin in plugins or []:\n'
                '            plugin.pytest_runtest_logfinish(item.nodeid, (str(item.path), 1, "test_stub"))\n'
                '    from pytest import ExitCode\n'
                '    for plugin in plugins or []:\n        plugin.pytest_sessionfinish(session, ExitCode.OK)\n'
                '    return ExitCode.OK\n'
            ),
            '_pytest/main.py': 'class Session:\n    pass\n',
            '_pytest/nodes.py': 'class Item:\n    pass\n',
        }
        for relative, content in pytest_files.items():
            path = site_packages / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        dist_info = site_packages / 'pytest-9.1.1.dist-info'
        dist_info.mkdir()
        (dist_info / 'METADATA').write_text(
            'Metadata-Version: 2.1\nName: pytest\nVersion: 9.1.1\n', encoding='utf-8',
        )
        (dist_info / 'top_level.txt').write_text('pytest\n_pytest\n', encoding='utf-8')
        record_entries = [*pytest_files, 'pytest-9.1.1.dist-info/METADATA',
                          'pytest-9.1.1.dist-info/top_level.txt', 'pytest-9.1.1.dist-info/RECORD']
        (dist_info / 'RECORD').write_text(
            ''.join(f'{name},,\n' for name in record_entries), encoding='utf-8',
        )
    return python


def write_test_wheel(wheel_dir, distribution, version, module_text, *, extra_files=None):
    module_name = distribution.replace('-', '_')
    wheel_name = f'{module_name}-{version}-py3-none-any.whl'
    wheel_path = wheel_dir / wheel_name
    dist_info = f'{module_name}-{version}.dist-info'
    with zipfile.ZipFile(wheel_path, 'w') as archive:
        archive.writestr(f'{module_name}/__init__.py', module_text)
        archive.writestr(
            f'{dist_info}/METADATA',
            f'Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n',
        )
        archive.writestr(
            f'{dist_info}/WHEEL',
            'Wheel-Version: 1.0\nGenerator: google-search-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        )
        archive.writestr(f'{dist_info}/RECORD', '')
        for name, content in (extra_files or {}).items():
            archive.writestr(name, content)
    return wheel_path


def locked_requests_module(preamble=''):
    return (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        f'{preamble}\n'
        "__version__ = '2.34.2'\n"
        "class RequestException(Exception):\n    pass\n"
        "class Timeout(RequestException):\n    pass\n"
        "class Session:\n"
        "    def __init__(self):\n        self.trust_env = True\n"
        "    def post(self, *args, **kwargs):\n        return None\n"
        "    def close(self):\n        pass\n"
    )


def write_local_runtime_lock(repo, *, module_overrides=None, extra_files=None):
    wheel_dir = repo / 'test-wheels'
    wheel_dir.mkdir()
    modules = {
        'certifi': "__version__ = '2026.7.22'\ndef where():\n    return __file__\n",
        'charset-normalizer': "__version__ = '3.5.1'\ndef from_bytes(value):\n    return value\n",
        'idna': (
            "__version__ = '3.19'\n"
            "def encode(value):\n    return str(value).encode('ascii')\n"
            "def decode(value):\n    return bytes(value).decode('ascii')\n"
        ),
        'requests': locked_requests_module(),
        'urllib3': "__version__ = '2.7.0'\nclass PoolManager:\n    pass\n",
    }
    modules.update(module_overrides or {})
    lines = ['--no-index', f'--find-links {wheel_dir.as_uri()}']
    for distribution, version in RUNTIME_PACKAGES.items():
        wheel = write_test_wheel(
            wheel_dir,
            distribution,
            version,
            modules[distribution],
            extra_files=(extra_files or {}).get(distribution),
        )
        digest = sha256(wheel.read_bytes()).hexdigest()
        lines.append(f'{distribution}=={version} --hash=sha256:{digest}')
    (repo / 'requirements.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def make_path_without_python(tmp_path):
    fake_bin = tmp_path / 'path-without-python'
    fake_bin.mkdir()
    for command in ('bash', 'dirname', 'find', 'id', 'readlink', 'stat'):
        target = shutil.which(command)
        assert target, command
        (fake_bin / command).symlink_to(target)
    return fake_bin


def test_shell_entrypoints_start_in_privileged_mode():
    expected_prelude = [
        '#!/bin/bash -p',
        'set +x',
        'unset BASH_ENV ENV CDPATH GLOBIGNORE',
        'for variable in "${!LD_@}"; do',
        '  unset -v "$variable"',
        'done',
        'unset variable',
        'unset GLIBC_TUNABLES GCONV_PATH',
        'unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE',
        'unset PYTHONPLATLIBDIR PYTHONSAFEPATH PYTHONWARNINGS PYTHONBREAKPOINT',
        'unset VIRTUAL_ENV __PYVENV_LAUNCHER__',
        'export -n SHELLOPTS 2>/dev/null || true',
        'set -euo pipefail',
    ]
    for name in ('install.sh', 'run.sh', 'check.sh'):
        path = ROOT / 'scripts' / name
        assert path.read_text(encoding='utf-8').splitlines()[:len(expected_prelude)] == expected_prelude
        assert path.stat().st_mode & 0o111 == 0o111


@pytest.mark.parametrize('entrypoint', ('install.sh', 'run.sh', 'check.sh'))
def test_shell_entrypoints_remove_all_loader_environment_before_first_child(tmp_path, entrypoint):
    repo = make_test_repo(tmp_path)
    script = repo / 'scripts' / entrypoint
    source = script.read_text(encoding='utf-8')
    probe = (
        'if [ "${LD_GOOGLE_SEARCH_SENTINEL+x}" = x ] || '
        '[ "${GLIBC_TUNABLES+x}" = x ] || [ "${GCONV_PATH+x}" = x ]; then\n'
        '  exit 97\n'
        'fi\n'
        'exit 0\n'
    )
    script.write_text(
        source.replace('set -euo pipefail\n', 'set -euo pipefail\n' + probe, 1),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}'],
        cwd=repo,
        env={
            'LD_GOOGLE_SEARCH_SENTINEL': 'must-not-survive',
            'GLIBC_TUNABLES': '',
            'GCONV_PATH': '',
        },
    )

    assert result.returncode == 0, (result.stdout, result.stderr)


def test_check_uses_only_token_bound_tasks_and_never_executes_display_path(tmp_path):
    repo = make_test_repo(tmp_path)
    display_marker = tmp_path / 'display-runtime-executed'
    display_runtime = tmp_path / 'display-runtime'
    display_runtime.write_text(
        f'#!/bin/sh\ntouch {display_marker}\nexit 97\n',
        encoding='utf-8',
    )
    display_runtime.chmod(0o755)
    task_log = tmp_path / 'runner-tasks.log'
    token = 'a' * 64
    runner = repo / 'scripts' / 'run.sh'
    runner.write_text(
        '#!/bin/bash -p\n'
        'set -eu\n'
        'arguments="$*"\n'
        'task=""\n'
        'runtime_info=0\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    --runtime-info) runtime_info=1; shift ;;\n'
        '    --task) task="$2"; shift 2 ;;\n'
        '    --expect-runtime-token) shift 2 ;;\n'
        '    *) shift ;;\n'
        '  esac\n'
        'done\n'
        'if [ "$runtime_info" -eq 1 ]; then\n'
        f"  printf '%s\\n' 'google-search-runtime-info-v1|venv|{display_runtime}|{token}'\n"
        '  exit 0\n'
        'fi\n'
        'printf "%s|%s\\n" "$task" "$arguments" >>"$RUNNER_TASK_LOG"\n'
        'case "$task" in\n'
        '  verify) ;;\n'
        "  check-ast) printf '%s\\n' 'AST OK: 0 files' ;;\n"
        "  check-pytest) printf '%s\\n' 'google-search-pytest-ok-v1' ;;\n"
        "  parsing) printf '%s\\n' '{}' ;;\n"
        "  check-result) printf '%s\\n' 'google-search-parsing-result-ok-v1' ;;\n"
        '  *) exit 97 ;;\n'
        'esac\n',
        encoding='utf-8',
    )
    runner.chmod(0o755)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--quiet'],
        cwd=repo,
        env={'RUNNER_TASK_LOG': str(task_log)},
    )

    assert result.returncode == 0, result.stderr
    assert not display_marker.exists()
    task_lines = task_log.read_text(encoding='utf-8').splitlines()
    assert [line.partition('|')[0] for line in task_lines] == [
        'verify', 'check-ast', 'check-pytest', 'parsing',
        'check-result', 'verify',
    ]
    for line in task_lines:
        task, _, arguments = line.partition('|')
        assert arguments.startswith(
            f'--venv --quiet --expect-runtime-token {token} --task {task} --'
        )
    check_source = (repo / 'scripts' / 'check.sh').read_text(encoding='utf-8')
    assert '--expect-runtime-token "$SELECTED_RUNTIME_TOKEN"' in check_source
    assert 'SELECTED_PY' not in check_source
    assert 'run_selected_script' not in check_source


def test_install_uses_only_a_token_bound_task_and_never_executes_display_path(tmp_path):
    repo = make_test_repo(tmp_path)
    display_marker = tmp_path / 'install-display-runtime-executed'
    display_runtime = repo / '.venv' / 'bin' / 'python'
    display_runtime.parent.mkdir(parents=True)
    display_runtime.write_text(
        f'#!/bin/sh\ntouch {display_marker}\nexit 97\n',
        encoding='utf-8',
    )
    display_runtime.chmod(0o755)
    task_log = tmp_path / 'install-runner-tasks.log'
    lock_leak_marker = tmp_path / 'install-runner-lock-leaked'
    token = 'b' * 64
    runner = repo / 'scripts' / 'run.sh'
    runner.write_text(
        '#!/bin/bash -p\n'
        'set -eu\n'
        'for descriptor in /proc/self/fd/*; do\n'
        '  target="$(readlink "$descriptor" 2>/dev/null)" || continue\n'
        '  if [ "$target" = "$EXPECTED_INSTALL_LOCK" ]; then\n'
        '    : >"$RUNNER_LOCK_LEAK_MARKER"\n'
        '  fi\n'
        'done\n'
        'arguments="$*"\n'
        'task=""\n'
        'runtime_info=0\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    --runtime-info) runtime_info=1; shift ;;\n'
        '    --task) task="$2"; shift 2 ;;\n'
        '    --expect-runtime-token) shift 2 ;;\n'
        '    *) shift ;;\n'
        '  esac\n'
        'done\n'
        'if [ "$runtime_info" -eq 1 ]; then\n'
        f"  printf '%s\\n' 'google-search-runtime-info-v1|venv|{display_runtime}|{token}'\n"
        '  exit 0\n'
        'fi\n'
        'printf "%s|%s\\n" "$task" "$arguments" >>"$RUNNER_TASK_LOG"\n'
        '[ "$task" = verify ]\n',
        encoding='utf-8',
    )
    runner.chmod(0o755)
    installer = repo / 'scripts' / 'install.sh'
    installer.write_text(
        installer.read_text(encoding='utf-8').replace(
            '\nselect_runtime\n\nif [ "$RUN_SMOKE_TEST"',
            '\nexec {INSTALL_LOCK_FD}>"$INSTALL_LOCK_PATH"\n'
            'select_runtime\n\nif [ "$RUN_SMOKE_TEST"',
            1,
        ),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--venv', '--json'],
        cwd=repo,
        env={
            'EXPECTED_INSTALL_LOCK': str(repo / '.venv.install.lock'),
            'RUNNER_LOCK_LEAK_MARKER': str(lock_leak_marker),
            'RUNNER_TASK_LOG': str(task_log),
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['python'] == str(display_runtime)
    assert not display_marker.exists()
    assert not lock_leak_marker.exists()
    task_lines = task_log.read_text(encoding='utf-8').splitlines()
    assert len(task_lines) == 1
    task, _, arguments = task_lines[0].partition('|')
    assert task == 'verify'
    assert arguments == (
        f'--venv --quiet --expect-runtime-token {token} --task verify --'
    )
    install_source = installer.read_text(encoding='utf-8')
    assert '--expect-runtime-token "$SELECTED_RUNTIME_TOKEN"' in install_source
    assert 'SELECTED_PY' not in install_source
    assert 'run_isolated_script' not in install_source


@pytest.mark.parametrize(
    'record_template',
    (
        'google-search-runtime-info-v0|system|/usr/bin/python3.11|{token}',
        'google-search-runtime-info-v1|invalid|/usr/bin/python3.11|{token}',
        'google-search-runtime-info-v1|system|relative/python3|{token}',
        'google-search-runtime-info-v1|system|/usr/bin/python3.11|not-a-token',
        'google-search-runtime-info-v1|system|/usr/bin/python3.11|{token}|extra',
    ),
)
def test_install_rejects_malformed_runtime_binding_records(tmp_path, record_template):
    repo = make_test_repo(tmp_path)
    record = record_template.format(token='c' * 64)
    runner = repo / 'scripts' / 'run.sh'
    runner.write_text(
        '#!/bin/bash -p\n'
        'set -eu\n'
        f"printf '%s\\n' {shlex.quote(record)}\n",
        encoding='utf-8',
    )
    runner.chmod(0o755)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--json'],
        cwd=repo,
    )

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload['ok'] is False
    assert payload['exitKind'] == 'dependency_error'
    assert payload['exitCode'] == 3


@pytest.mark.parametrize(
    'unsafe_path',
    (
        '/usr/bin/control\x1bpython',
        '/usr/bin/c1\u0085python',
        '/usr/bin/arabic-mark\u061cpython',
        '/usr/bin/left-to-right-mark\u200epython',
        '/usr/bin/bidi-override\u202epython',
        '/usr/bin/bidi-isolate\u2066python',
    ),
)
@pytest.mark.parametrize(
    ('entrypoint', 'arguments', 'expected_code'),
    (
        ('install.sh', (), 3),
        ('check.sh', ('--quiet',), 1),
    ),
)
def test_runtime_consumers_reject_terminal_unsafe_display_paths(
    tmp_path,
    unsafe_path,
    entrypoint,
    arguments,
    expected_code,
):
    repo = make_test_repo(tmp_path)
    token = 'd' * 64
    runner = repo / 'scripts' / 'run.sh'
    record = f'google-search-runtime-info-v1|system|{unsafe_path}|{token}'
    runner.write_text(
        '#!/bin/bash -p\n'
        'set -eu\n'
        f"printf '%s\\n' {shlex.quote(record)}\n",
        encoding='utf-8',
    )
    runner.chmod(0o755)

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments],
        cwd=repo,
    )

    assert result.returncode == expected_code
    assert unsafe_path not in result.stdout
    assert unsafe_path not in result.stderr


def test_supported_shell_entrypoints_ignore_startup_environment(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    entrypoints = (
        ('run.sh', ('--venv', '--runtime-info')),
        ('install.sh', ('--venv', '--json')),
        ('check.sh', ('--venv', '--quiet')),
    )

    for invocation in ('explicit', 'shebang'):
        for entrypoint, arguments in entrypoints:
            label = f'{invocation}-{entrypoint}'
            bash_env_marker = tmp_path / f'{label}-bash-env'
            stat_marker = tmp_path / f'{label}-stat-function'
            trace_path = tmp_path / f'{label}-xtrace'
            bash_env_payload = tmp_path / f'{label}-bash-env-payload'
            bash_env_payload.write_text(
                '/bin/printf loaded > "$BASH_ENV_MARKER"\n',
                encoding='utf-8',
            )
            bash_env_fd = os.open(bash_env_payload, os.O_RDONLY)
            trace_fd = os.open(
                trace_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            command = [str(repo / 'scripts' / entrypoint), *arguments]
            if invocation == 'explicit':
                command = ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments]
            try:
                result = run_command(
                    command,
                    cwd=repo,
                    env={
                        'BASH_ENV': f'/proc/self/fd/{bash_env_fd}',
                        'BASH_ENV_MARKER': str(bash_env_marker),
                        'BASH_FUNC_stat%%': (
                            '() { /bin/printf imported > "$STAT_FUNCTION_MARKER"; '
                            'return 97; }'
                        ),
                        'STAT_FUNCTION_MARKER': str(stat_marker),
                        'SHELLOPTS': 'xtrace',
                        'BASH_XTRACEFD': str(trace_fd),
                        'PS4': 'UNTRUSTED_XTRACE ',
                    },
                    pass_fds=(bash_env_fd, trace_fd),
                    timeout=CHECK_INTEGRATION_TIMEOUT,
                )
            finally:
                os.close(bash_env_fd)
                os.close(trace_fd)

            assert result.returncode == 0, (label, result.stdout, result.stderr)
            assert not bash_env_marker.exists(), label
            assert not stat_marker.exists(), label
            assert trace_path.read_bytes() == b'', label


def test_run_selects_healthy_venv_and_executes_search(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)

    mode, display_path, _ = runtime_info(repo)
    assert mode == 'venv'
    assert display_path == python

    result = run_command(['/bin/bash', '-p', 'scripts/run.sh', 'web', 'OpenClaw'], cwd=repo)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['web', 'OpenClaw']


def test_run_runtime_info_rejects_terminal_unsafe_selected_path(tmp_path):
    unsafe_parent = tmp_path / 'bidi-override-\u202e'
    unsafe_parent.mkdir()
    repo = make_test_repo(unsafe_parent)
    create_stub_runtime(repo)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'],
        cwd=repo,
    )

    assert result.returncode == 3
    assert result.stdout == ''
    assert '\u202e' not in result.stderr


def test_run_runtime_info_allows_regular_utf8_selected_path(tmp_path):
    unicode_parent = tmp_path / '\u5b89\u5168\u8def\u5f84'
    unicode_parent.mkdir()
    repo = make_test_repo(unicode_parent)
    python = create_stub_runtime(repo)

    mode, display_path, token = runtime_info(repo, '--venv')

    assert mode == 'venv'
    assert display_path == python
    assert re.fullmatch(r'[0-9a-f]{64}', token)


def runtime_info(repo, *arguments):
    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', *arguments, '--runtime-info'],
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    sentinel, mode, display_path, token = result.stdout.strip().split('|')
    assert sentinel == 'google-search-runtime-info-v1'
    assert mode in {'system', 'venv'}
    assert Path(display_path).is_absolute()
    assert re.fullmatch(r'[0-9a-f]{64}', token)
    return mode, Path(display_path), token


def run_trusted_tree_probe(repo, tree):
    runner = repo / 'scripts' / 'run.sh'
    source = runner.read_text(encoding='utf-8')
    probe = (
        f'trusted_tree_is_safe {shlex.quote(str(tree))} || exit 97\n'
        f'tree_metadata_digest {shlex.quote(str(tree))} 1\n'
        'exit 0\n\n'
    )
    runner.write_text(
        source.replace('while [ "$#" -gt 0 ]; do', probe + 'while [ "$#" -gt 0 ]; do', 1),
        encoding='utf-8',
    )
    return run_command(['/bin/bash', '-p', 'scripts/run.sh'], cwd=repo)


def run_installer_trusted_tree_probe(repo, tree):
    installer = repo / 'scripts' / 'install.sh'
    source = installer.read_text(encoding='utf-8')
    probe = (
        f'trusted_tree_is_safe {shlex.quote(str(tree))} || exit 97\n'
        f'tree_payload_digest {shlex.quote(str(tree))} 1\n'
        'exit 0\n\n'
    )
    installer.write_text(
        source.replace(
            '# Detect machine-output intent before validation',
            probe + '# Detect machine-output intent before validation',
            1,
        ),
        encoding='utf-8',
    )
    return run_command(['/bin/bash', '-p', 'scripts/install.sh'], cwd=repo)


def test_run_trusted_stdlib_tree_accepts_a_safe_cross_device_file_symlink(tmp_path):
    if not Path('/dev/shm').is_dir():
        pytest.skip('/dev/shm is unavailable')
    repo = make_test_repo(tmp_path)
    tree = tmp_path / 'trusted-stdlib'
    tree.mkdir()
    with tempfile.TemporaryDirectory(prefix='google-search-symlink-', dir='/dev/shm') as target_dir:
        target = Path(target_dir) / 'sitecustomize.py'
        target.write_text('# trusted cross-device target\n', encoding='utf-8')
        target.chmod(0o600)
        if target.stat().st_dev == tree.stat().st_dev:
            pytest.skip('/dev/shm is not on a separate device')
        (tree / 'sitecustomize.py').symlink_to(target)

        result = run_trusted_tree_probe(repo, tree)

    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r'[0-9a-f]{64}\n', result.stdout)


def test_run_trusted_stdlib_tree_rejects_a_directory_symlink(tmp_path):
    repo = make_test_repo(tmp_path)
    tree = tmp_path / 'trusted-stdlib'
    target = tmp_path / 'directory-target'
    tree.mkdir()
    target.mkdir()
    (target / 'module.py').write_text('# must not be traversed\n', encoding='utf-8')
    (tree / 'linked-package').symlink_to(target, target_is_directory=True)

    result = run_trusted_tree_probe(repo, tree)

    assert result.returncode == 97
    assert result.stdout == ''


def test_install_trusted_stdlib_tree_accepts_a_safe_cross_device_file_symlink(tmp_path):
    if not Path('/dev/shm').is_dir():
        pytest.skip('/dev/shm is unavailable')
    repo = make_test_repo(tmp_path)
    tree = tmp_path / 'trusted-installer-stdlib'
    tree.mkdir()
    with tempfile.TemporaryDirectory(prefix='google-search-install-symlink-', dir='/dev/shm') as target_dir:
        target = Path(target_dir) / 'sitecustomize.py'
        target.write_text('# trusted cross-device target\n', encoding='utf-8')
        target.chmod(0o600)
        if target.stat().st_dev == tree.stat().st_dev:
            pytest.skip('/dev/shm is not on a separate device')
        (tree / 'sitecustomize.py').symlink_to(target)

        result = run_installer_trusted_tree_probe(repo, tree)

    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r'[0-9a-f]{64}\n', result.stdout)


def test_install_trusted_stdlib_tree_rejects_a_directory_symlink(tmp_path):
    repo = make_test_repo(tmp_path)
    tree = tmp_path / 'trusted-installer-stdlib'
    target = tmp_path / 'installer-directory-target'
    tree.mkdir()
    target.mkdir()
    (target / 'module.py').write_text('# must not be traversed\n', encoding='utf-8')
    (tree / 'linked-package').symlink_to(target, target_is_directory=True)

    result = run_installer_trusted_tree_probe(repo, tree)

    assert result.returncode == 97
    assert result.stdout == ''


@pytest.mark.skipif(
    not Path('/usr/lib/python3.11/sitecustomize.py').is_symlink(),
    reason='Debian Python 3.11 stdlib file symlinks are unavailable',
)
def test_run_trusted_stdlib_tree_accepts_debian_file_symlinks(tmp_path):
    repo = make_test_repo(tmp_path)

    result = run_trusted_tree_probe(repo, Path('/usr/lib/python3.11'))

    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r'[0-9a-f]{64}\n', result.stdout)


def test_run_runtime_info_token_authorizes_only_the_selected_runtime(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)

    mode, display_path, token = runtime_info(repo, '--venv')
    assert mode == 'venv'
    assert display_path == python

    result = run_command(
        [
            '/bin/bash', '-p', 'scripts/run.sh', '--venv',
            '--expect-runtime-token', token, '--task', 'verify',
        ],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
    assert result.stderr == ''


def test_run_runtime_token_rejects_replaced_venv_python_before_execution(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    _, _, token = runtime_info(repo, '--venv')
    marker = tmp_path / 'replacement-python-executed'

    python.unlink()
    python.write_text(f'#!/bin/sh\n: > {marker}\nexit 97\n', encoding='utf-8')
    python.chmod(0o700)

    result = run_command(
        [
            '/bin/bash', '-p', 'scripts/run.sh', '--venv',
            '--expect-runtime-token', token, '--task', 'verify',
        ],
        cwd=repo,
    )

    assert result.returncode == 3
    assert 'changed before its health probe' in result.stderr
    assert not marker.exists()


def test_run_runtime_token_mismatch_fails_closed(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    _, _, token = runtime_info(repo, '--venv')
    wrong_token = ('0' if token[0] != '0' else '1') + token[1:]

    result = run_command(
        [
            '/bin/bash', '-p', 'scripts/run.sh', '--venv',
            '--expect-runtime-token', wrong_token, '--task', 'verify',
        ],
        cwd=repo,
    )

    assert result.returncode == 3
    assert 'changed before its health probe' in result.stderr


@pytest.mark.parametrize(
    'relative_path',
    (
        'README.md',
        'tests/test_placeholder.py',
        'references/example.md',
    ),
)
def test_run_runtime_token_rejects_same_size_source_mutation(tmp_path, relative_path):
    repo = make_test_repo(tmp_path)
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text('source snapshot fixture\n', encoding='utf-8')
    create_stub_runtime(repo)
    _, _, token = runtime_info(repo, '--venv')
    original = target.read_bytes()
    assert original
    target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    assert target.stat().st_size == len(original)

    result = run_command(
        [
            '/bin/bash', '-p', 'scripts/run.sh', '--venv',
            '--expect-runtime-token', token, '--task', 'verify',
        ],
        cwd=repo,
    )

    assert result.returncode == 3
    assert 'changed before its health probe' in result.stderr


def test_run_source_snapshot_rejects_an_unknown_root_release_candidate(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    (repo / 'release-candidate.txt').write_text('untracked release input\n', encoding='utf-8')

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'],
        cwd=repo,
    )

    assert result.returncode == 3
    assert 'source tree or its parent chain is unsafe' in result.stderr


def test_run_source_snapshot_ignores_private_and_generated_artifacts(tmp_path):
    repo = make_test_repo(tmp_path)
    ignored_files = {
        '.coverage': 'coverage state one\n',
        '.coverage.worker': 'worker state one\n',
        'coverage.xml': '<coverage />\n',
        '.DS_Store': 'finder state\n',
        '.env': 'SERPER_API_KEY=placeholder-one\n',
        'private.key': 'placeholder private key one\n',
        'credentials.json': '{"placeholder": 1}\n',
    }
    for relative_path, content in ignored_files.items():
        (repo / relative_path).write_text(content, encoding='utf-8')
    for relative_path in ('.vscode/settings.json', '.idea/workspace.xml'):
        path = repo / relative_path
        path.parent.mkdir(exist_ok=True)
        path.write_text('{}\n', encoding='utf-8')
    create_stub_runtime(repo)
    _, _, token = runtime_info(repo, '--venv')

    for relative_path, content in ignored_files.items():
        (repo / relative_path).write_text(content.replace('one', 'two'), encoding='utf-8')
    result = run_command(
        [
            '/bin/bash', '-p', 'scripts/run.sh', '--venv',
            '--expect-runtime-token', token, '--task', 'verify',
        ],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr


def test_run_source_snapshot_rejects_more_than_ten_thousand_entries(tmp_path):
    repo = make_test_repo(tmp_path)
    references = repo / 'references'
    references.mkdir()
    for index in range(10_001):
        (references / f'entry-{index:05d}').touch()
    create_stub_runtime(repo)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'],
        cwd=repo,
    )

    assert result.returncode == 3
    assert 'source tree or its parent chain is unsafe' in result.stderr


@pytest.mark.parametrize('suffix', ('.pyc', '.pyd', '.pyo'))
def test_run_source_snapshot_rejects_standalone_bytecode_in_scripts(tmp_path, suffix):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = tmp_path / 'bytecode-executed'
    (repo / 'scripts' / f'injected{suffix}').write_bytes(
        f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'.encode('utf-8')
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'],
        cwd=repo,
    )

    assert result.returncode == 3
    assert 'source tree or its parent chain is unsafe' in result.stderr
    assert not marker.exists()


def test_run_token_bound_tasks_reject_unknown_roles_and_arguments_before_python_startup(
    tmp_path,
):
    task_argument_sets = (
        ('unknown-role',),
        ('-c',),
        ('-m',),
        ('/tmp/arbitrary-script.py',),
        ('verify', 'extra'),
        ('verify', '-c', 'print(1)'),
        ('verify', '-m', 'module'),
        ('check-result', 'smoke', 'relative-result.json'),
        ('check-result', 'unknown-kind', '/tmp/result.json'),
        ('check-result', 'smoke', '/tmp/result.json', 'extra'),
    )
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    _, _, token = runtime_info(repo, '--venv')
    marker = tmp_path / 'python-started'

    python.unlink()
    python.write_text(f'#!/bin/sh\n: > {marker}\nexit 97\n', encoding='utf-8')
    python.chmod(0o700)
    for task_arguments in task_argument_sets:
        result = run_command(
            [
                '/bin/bash', '-p', 'scripts/run.sh', '--venv',
                '--expect-runtime-token', token, '--task', *task_arguments,
            ],
            cwd=repo,
        )

        assert result.returncode == 2, (task_arguments, result.stderr)
        assert 'invalid task role or arguments' in result.stderr
    assert not marker.exists()


def test_run_release_source_task_rejects_missing_extra_option_and_unseparated_arguments(
    tmp_path,
):
    task_suffixes = (
        ('check-release-source',),
        ('check-release-source', '--', '/tmp/head-tree.z'),
        ('check-release-source', '--', '/tmp/head-tree.z', '/tmp/index-stage.z', 'extra'),
        ('check-release-source', '--', '--head-tree.z', '/tmp/index-stage.z'),
        ('check-release-source', '/tmp/head-tree.z', '/tmp/index-stage.z'),
        ('check-release-source', '--', 'relative-head-tree.z', '/tmp/index-stage.z'),
    )
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    _, _, token = runtime_info(repo, '--venv')
    marker = tmp_path / 'python-started'
    python.unlink()
    python.write_text(f'#!/bin/sh\n: > {marker}\nexit 97\n', encoding='utf-8')
    python.chmod(0o700)
    for task_suffix in task_suffixes:
        result = run_command(
            [
                '/bin/bash', '-p', 'scripts/run.sh', '--venv',
                '--expect-runtime-token', token, '--task', *task_suffix,
            ],
            cwd=repo,
        )

        assert result.returncode == 2, (task_suffix, result.stderr)
        assert 'invalid task role or arguments' in result.stderr
    assert not marker.exists()


def test_run_release_source_task_maps_only_two_manifests_to_the_fixed_helper(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    protocol = repo / 'scripts' / 'check_protocol.py'
    protocol.write_text(
        'import json, sys\nprint(json.dumps(sys.argv[1:]))\n',
        encoding='utf-8',
    )
    head_tree = tmp_path / 'head-tree.z'
    index_stage = tmp_path / 'index-stage.z'
    _, _, token = runtime_info(repo, '--venv')

    result = run_command(
        [
            '/bin/bash', '-p', 'scripts/run.sh', '--venv',
            '--expect-runtime-token', token, '--task', 'check-release-source', '--',
            str(head_tree), str(index_stage),
        ],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        'release-source', '--base-dir', str(repo), '--', str(head_tree), str(index_stage),
    ]


@pytest.mark.parametrize(
    'arguments',
    (
        ('--venv', '--task', 'verify'),
        ('--venv', '--expect-runtime-token', '0' * 64),
        ('--expect-runtime-token', '0' * 64, '--task', 'verify'),
        ('--venv', '--expect-runtime-token', 'not-a-token', '--task', 'verify'),
        ('--venv', '--runtime-info', '--expect-runtime-token', '0' * 64, '--task', 'verify'),
        ('--venv', '--runtime-info', 'unexpected-search-argument'),
    ),
)
def test_run_rejects_incomplete_or_conflicting_runner_protocol_arguments(tmp_path, arguments):
    repo = make_test_repo(tmp_path)

    result = run_command(['/bin/bash', '-p', 'scripts/run.sh', *arguments], cwd=repo)

    assert result.returncode == 2


def test_run_refuses_symlink_venv_without_executing_it(tmp_path):
    repo = make_test_repo(tmp_path)
    external = tmp_path / 'external-venv'
    (external / 'bin').mkdir(parents=True)
    marker = tmp_path / 'executed'
    fake_python = external / 'bin' / 'python'
    fake_python.write_text(f'#!/bin/sh\ntouch {marker}\nexit 0\n', encoding='utf-8')
    fake_python.chmod(0o700)
    (external / 'pyvenv.cfg').write_text('version = 3.11.0\n', encoding='utf-8')
    (repo / '.venv').symlink_to(external, target_is_directory=True)

    result = run_command(['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'], cwd=repo)
    assert result.returncode == 3
    assert not marker.exists()


@pytest.mark.skipif(os.geteuid() != 0, reason='creating a foreign-owned runtime requires root')
def test_run_rejects_venv_python_beneath_a_foreign_owned_parent(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    foreign_bin = tmp_path / 'foreign-bin'
    foreign_bin.mkdir(mode=0o755)
    copied_python = foreign_bin / 'python'
    shutil.copy2(Path(sys.executable).resolve(), copied_python)
    copied_python.chmod(0o755)
    foreign_uid = next(uid for uid in (65534, 65533, 12345) if uid not in {0, os.geteuid()})
    os.chown(foreign_bin, foreign_uid, -1)
    python.unlink()
    python.symlink_to(copied_python)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'],
        cwd=repo,
    )

    assert result.returncode == 3


@pytest.mark.skipif(os.geteuid() != 0, reason='creating a foreign-owned runtime requires root')
def test_run_rejects_an_intermediate_python_symlink_beneath_a_foreign_owned_parent(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    foreign_bin = tmp_path / 'foreign-bin'
    foreign_bin.mkdir(mode=0o755)
    intermediate = foreign_bin / 'python'
    intermediate.symlink_to(Path(sys.executable).resolve())
    foreign_uid = next(uid for uid in (65534, 65533, 12345) if uid not in {0, os.geteuid()})
    os.chown(foreign_bin, foreign_uid, -1)
    python.unlink()
    python.symlink_to(intermediate)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'],
        cwd=repo,
    )

    assert result.returncode == 3


@pytest.mark.skipif(os.geteuid() != 0, reason='creating a foreign-owned runtime requires root')
@pytest.mark.parametrize(
    ('entrypoint', 'arguments'),
    (
        ('run.sh', ('--venv', '--runtime-info')),
        ('install.sh', ('--venv', '--json')),
    ),
)
def test_shell_runtime_validation_rejects_foreign_owned_pyvenv_home_before_execution(
    tmp_path,
    entrypoint,
    arguments,
):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    marker = tmp_path / f'{entrypoint}-unsafe-home-executed'
    foreign_home = tmp_path / 'foreign-home'
    foreign_home.mkdir(mode=0o755)
    foreign_uid = next(uid for uid in (65534, 65533, 12345) if uid not in {0, os.geteuid()})
    os.chown(foreign_home, foreign_uid, -1)
    config = repo / '.venv' / 'pyvenv.cfg'
    config.write_text(
        re.sub(r'^home\s*=.*$', f'home = {foreign_home}', config.read_text(encoding='utf-8'),
               count=1, flags=re.MULTILINE),
        encoding='utf-8',
    )
    python.unlink()
    python.write_text(f'#!/bin/sh\n: > {marker}\nexit 97\n', encoding='utf-8')
    python.chmod(0o700)

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments],
        cwd=repo,
    )

    assert result.returncode != 0
    assert not marker.exists()


@pytest.mark.parametrize(
    ('entrypoint', 'arguments'),
    (
        ('run.sh', ('--venv', '--runtime-info')),
        ('install.sh', ('--venv', '--json')),
    ),
)
def test_shell_runtime_accepts_safe_intermediate_symlink_in_pyvenv_home(
    tmp_path,
    entrypoint,
    arguments,
):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    config = repo / '.venv' / 'pyvenv.cfg'
    config_text = config.read_text(encoding='utf-8')
    home_match = re.search(r'^home\s*=\s*(.+)$', config_text, flags=re.MULTILINE)
    assert home_match is not None
    original_home = Path(home_match.group(1))
    alias = tmp_path / 'trusted-python-prefix'
    alias.symlink_to(original_home.parent, target_is_directory=True)
    aliased_home = alias / original_home.name
    config.write_text(
        re.sub(r'^home\s*=.*$', f'home = {aliased_home}', config_text, count=1,
               flags=re.MULTILINE),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments],
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    if entrypoint == 'run.sh':
        sentinel, mode, display_path, token = result.stdout.strip().split('|')
        assert sentinel == 'google-search-runtime-info-v1'
        assert mode == 'venv'
        assert display_path == str(python)
        assert re.fullmatch(r'[0-9a-f]{64}', token)
    else:
        assert json.loads(result.stdout)['ok'] is True


@pytest.mark.skipif(os.geteuid() != 0, reason='changing symlink ownership requires root')
@pytest.mark.parametrize(
    ('entrypoint', 'arguments'),
    (
        ('run.sh', ('--venv', '--runtime-info')),
        ('install.sh', ('--venv', '--json')),
    ),
)
def test_shell_runtime_rejects_foreign_owned_intermediate_symlink_in_pyvenv_home(
    tmp_path,
    entrypoint,
    arguments,
):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    config = repo / '.venv' / 'pyvenv.cfg'
    config_text = config.read_text(encoding='utf-8')
    home_match = re.search(r'^home\s*=\s*(.+)$', config_text, flags=re.MULTILINE)
    assert home_match is not None
    original_home = Path(home_match.group(1))
    alias = tmp_path / 'foreign-python-prefix'
    alias.symlink_to(original_home.parent, target_is_directory=True)
    foreign_uid = next(uid for uid in (65534, 65533, 12345) if uid not in {0, os.geteuid()})
    os.lchown(alias, foreign_uid, -1)
    config.write_text(
        re.sub(r'^home\s*=.*$', f'home = {alias / original_home.name}', config_text, count=1,
               flags=re.MULTILINE),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments],
        cwd=repo,
    )

    assert result.returncode != 0


@pytest.mark.skipif(os.geteuid() != 0, reason='creating a foreign-owned runtime requires root')
@pytest.mark.parametrize(
    ('entrypoint', 'arguments'),
    (
        ('run.sh', ('--venv', '--runtime-info')),
        ('install.sh', ('--venv', '--json')),
    ),
)
def test_shell_runtime_validation_rejects_foreign_owned_pyvenv_executable_before_execution(
    tmp_path,
    entrypoint,
    arguments,
):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    marker = tmp_path / f'{entrypoint}-unsafe-executable-ran'
    foreign_bin = tmp_path / 'foreign-bin'
    foreign_bin.mkdir(mode=0o755)
    foreign_python = foreign_bin / 'python'
    shutil.copy2(Path(sys.executable).resolve(), foreign_python)
    foreign_python.chmod(0o755)
    foreign_uid = next(uid for uid in (65534, 65533, 12345) if uid not in {0, os.geteuid()})
    os.chown(foreign_bin, foreign_uid, -1)
    config = repo / '.venv' / 'pyvenv.cfg'
    config_text = re.sub(
        r'^executable\s*=.*\n?', '', config.read_text(encoding='utf-8'), flags=re.MULTILINE,
    )
    config.write_text(config_text + f'executable = {foreign_python}\n', encoding='utf-8')
    python.unlink()
    python.write_text(f'#!/bin/sh\n: > {marker}\nexit 97\n', encoding='utf-8')
    python.chmod(0o700)

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments],
        cwd=repo,
    )

    assert result.returncode != 0
    assert not marker.exists()


@pytest.mark.skipif(not Path('/usr/bin/python3.11').is_file(), reason='Debian Python 3.11 is unavailable')
@pytest.mark.parametrize(
    ('entrypoint', 'arguments'),
    (
        ('run.sh', ('--venv', '--runtime-info')),
        ('install.sh', ('--venv', '--json')),
    ),
)
def test_shell_runtime_rejects_unsafe_pyvenv_stdlib_before_encoding_startup(
    tmp_path,
    entrypoint,
    arguments,
):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    marker = tmp_path / f'{entrypoint}-unsafe-stdlib-executed'
    fake_prefix = tmp_path / 'fake-prefix'
    fake_home = fake_prefix / 'bin'
    stdlib = fake_prefix / 'lib' / 'python3.11'
    encodings = stdlib / 'encodings'
    (stdlib / 'lib-dynload').mkdir(parents=True)
    encodings.mkdir()
    (stdlib / 'os.py').write_text('# landmark\n', encoding='utf-8')
    (encodings / '__init__.py').write_text(
        f'open({str(marker)!r}, "w", encoding="utf-8").write("executed")\n',
        encoding='utf-8',
    )
    fake_home.mkdir()
    (fake_prefix / 'lib').chmod(0o777)

    config = repo / '.venv' / 'pyvenv.cfg'
    config.write_text(
        f'home = {fake_home}\n'
        'include-system-site-packages = false\n'
        'version = 3.11.0\n'
        'executable = /usr/bin/python3.11\n',
        encoding='utf-8',
    )
    python.unlink()
    python.symlink_to('/usr/bin/python3.11')

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments],
        cwd=repo,
    )

    assert result.returncode != 0
    assert not marker.exists()


@pytest.mark.skipif(not Path('/usr/bin/python3.11').is_file(), reason='Debian Python 3.11 is unavailable')
@pytest.mark.parametrize(
    ('entrypoint', 'arguments'),
    (
        ('run.sh', ('--system', '--runtime-info')),
        ('install.sh', ('--system', '--json')),
    ),
)
@pytest.mark.parametrize('unsafe_layout', ('writable-stdlib', 'python-path-config', 'pyvenv-config'))
def test_shell_rejects_unsafe_system_stdlib_layout_before_encoding_startup(
    tmp_path,
    entrypoint,
    arguments,
    unsafe_layout,
):
    repo = make_test_repo(tmp_path)
    marker = tmp_path / f'{entrypoint}-{unsafe_layout}-encoding-executed'
    prefix = tmp_path / 'python-prefix'
    bin_dir = prefix / 'bin'
    stdlib = prefix / 'lib' / 'python3.11'
    encodings = stdlib / 'encodings'
    bin_dir.mkdir(parents=True)
    (stdlib / 'lib-dynload').mkdir(parents=True)
    encodings.mkdir()
    shutil.copy2('/usr/bin/python3.11', bin_dir / 'python3.11')
    (bin_dir / 'python3').symlink_to('python3.11')
    (stdlib / 'os.py').write_text('# static stdlib landmark\n', encoding='utf-8')
    (encodings / '__init__.py').write_text(
        f'open({str(marker)!r}, "w", encoding="utf-8").write("executed")\n',
        encoding='utf-8',
    )
    if unsafe_layout == 'writable-stdlib':
        (prefix / 'lib').chmod(0o777)
    elif unsafe_layout == 'python-path-config':
        (bin_dir / 'python3._pth').write_text(f'{stdlib}\n', encoding='utf-8')
    else:
        (prefix / 'pyvenv.cfg').write_text(f'home = {bin_dir}\n', encoding='utf-8')

    script = repo / 'scripts' / entrypoint
    script.write_text(
        script.read_text(encoding='utf-8').replace(
            "PATH='/usr/local/bin:/usr/bin:/bin'",
            f"PATH='{bin_dir}:/usr/local/bin:/usr/bin:/bin'",
            1,
        ),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments],
        cwd=repo,
    )

    assert result.returncode != 0
    assert not marker.exists()


def test_run_binds_the_venv_python_symlink_across_the_health_probe(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    marker = tmp_path / 'replacement-python-executed'
    replacement = tmp_path / 'replacement-python'
    replacement.write_text(f'#!/bin/sh\n: > {marker}\nexit 97\n', encoding='utf-8')
    replacement.chmod(0o700)
    site_packages = Path(
        subprocess.run(
            [str(python), '-I', '-c', 'import site; print(site.getsitepackages()[0])'],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    requests_module = site_packages / 'requests' / '__init__.py'
    requests_module.write_text(
        'import pathlib, sys\n'
        f'_replacement = pathlib.Path({str(replacement)!r})\n'
        '_venv_python = pathlib.Path(sys.executable)\n'
        '_venv_python.unlink()\n'
        '_venv_python.symlink_to(_replacement)\n'
        + requests_module.read_text(encoding='utf-8'),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--venv', 'web', 'OpenClaw'],
        cwd=repo,
    )

    assert result.returncode == 3
    assert not marker.exists()


def test_run_detects_same_size_site_file_mutation_during_health_probe(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    marker = tmp_path / 'search-executed-after-site-mutation'
    (repo / 'scripts' / 'search.py').write_text(
        f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n',
        encoding='utf-8',
    )
    site_packages = Path(
        subprocess.run(
            [str(python), '-I', '-c', 'import site; print(site.getsitepackages()[0])'],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    requests_module = site_packages / 'requests' / '__init__.py'
    certifi_module = site_packages / 'certifi' / '__init__.py'
    original = requests_module.read_text(encoding='utf-8')
    requests_module.write_text(
        'from pathlib import Path\n'
        f'_target = Path({str(certifi_module)!r})\n'
        '_payload = _target.read_bytes()\n'
        '_target.write_bytes(bytes([_payload[0] ^ 1]) + _payload[1:])\n'
        + original,
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--venv', 'web', 'OpenClaw'],
        cwd=repo,
    )

    assert result.returncode == 3
    assert not marker.exists()


@pytest.mark.parametrize(
    ('entrypoint', 'arguments'),
    (
        ('run.sh', ('--venv', 'web', 'OpenClaw')),
        ('install.sh', ('--venv', '--smoke-test', '--quiet')),
        ('check.sh', ('--venv', '--quiet')),
    ),
)
def test_shell_entrypoints_do_not_allow_scripts_to_shadow_stdlib(
    tmp_path,
    entrypoint,
    arguments,
):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    marker = tmp_path / f'{entrypoint}-shadow-json-imported'
    (repo / 'scripts' / 'json.py').write_text(
        'import os, pathlib\n'
        'pathlib.Path(os.environ["STDLIB_SHADOW_MARKER"]).write_text("imported")\n'
        'raise SystemExit(97)\n',
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments],
        cwd=repo,
        env={
            'SERPER_API_KEY': 'test-only-key-123',
            'STDLIB_SHADOW_MARKER': str(marker),
        },
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_run_rejects_group_writable_site_packages(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    site_packages = Path(
        subprocess.run(
            [str(python), '-I', '-c', 'import site; print(site.getsitepackages()[0])'],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    site_packages.chmod(0o775)

    result = run_command(['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'], cwd=repo)
    assert result.returncode == 3


def test_run_rejects_noop_system_python_that_returns_success(tmp_path):
    repo = make_test_repo(tmp_path)
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake_python = fake_bin / 'python3'
    fake_python.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    fake_python.chmod(0o700)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--runtime-info'],
        cwd=repo,
        env={'PATH': f'{fake_bin}:{os.environ["PATH"]}'},
    )
    assert result.returncode == 3
    assert result.stdout == ''


def test_run_rejects_setid_system_python_without_executing_it(tmp_path):
    repo = make_test_repo(tmp_path)
    fake_bin = tmp_path / 'setid-bin'
    fake_bin.mkdir()
    marker = tmp_path / 'setid-python-ran'
    fake_python = fake_bin / 'python3'
    fake_python.write_text(f'#!/bin/sh\ntouch {marker}\nexit 0\n', encoding='utf-8')
    fake_python.chmod(0o4755)
    runner = repo / 'scripts' / 'run.sh'
    runner.write_text(
        runner.read_text(encoding='utf-8').replace(
            "PATH='/usr/local/bin:/usr/bin:/bin'",
            f"PATH={str(fake_bin)!r}",
            1,
        ),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--system', '--runtime-info'],
        cwd=repo,
    )

    assert result.returncode == 3
    assert not marker.exists()


def test_run_system_probes_use_no_site_startup_and_hide_api_keys(tmp_path):
    repo = make_test_repo(tmp_path)
    fake_bin = tmp_path / 'guarded-bin'
    fake_bin.mkdir()
    missing_no_site = tmp_path / 'missing-no-site'
    leaked_keys = tmp_path / 'leaked-keys'
    fake_python = fake_bin / 'python3'
    fake_python.write_text(
        '#!/bin/sh\n'
        'has_no_site=0\n'
        'for argument in "$@"; do\n'
        '  [ "$argument" != "-S" ] || has_no_site=1\n'
        'done\n'
        f'[ "$has_no_site" -eq 1 ] || : > {missing_no_site}\n'
        f'if [ -n "${{SERPER_API_KEY:-}}${{SERPER_API_KEYS:-}}" ]; then env > {leaked_keys}; fi\n'
        f'exec {sys.executable} "$@"\n',
        encoding='utf-8',
    )
    fake_python.chmod(0o700)
    runner = repo / 'scripts' / 'run.sh'
    runner.write_text(
        runner.read_text(encoding='utf-8').replace(
            "PATH='/usr/local/bin:/usr/bin:/bin'",
            f"PATH={str(fake_bin)!r}",
            1,
        ),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/run.sh', '--system', '--runtime-info'],
        cwd=repo,
        env={
            'SERPER_API_KEY': 'must-not-reach-system-probe',
            'SERPER_API_KEYS': 'must-not-reach-system-probes',
        },
    )

    # Python 3.14 initializes the venv prefix even with -S, so this controlled
    # wrapper can expose a valid runtime there while older versions reject it.
    assert result.returncode in {0, 3}
    if result.returncode == 0:
        sentinel, mode, display_path, token = result.stdout.strip().split('|')
        assert sentinel == 'google-search-runtime-info-v1'
        assert mode == 'system'
        assert display_path == str(fake_python)
        assert re.fullmatch(r'[0-9a-f]{64}', token)
    else:
        assert result.stdout == ''
    assert not missing_no_site.exists()
    assert not leaked_keys.exists()


def test_run_rejects_venv_with_system_site_packages_enabled(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    config = repo / '.venv' / 'pyvenv.cfg'
    config.write_text(
        config.read_text(encoding='utf-8').replace(
            'include-system-site-packages = false',
            'include-system-site-packages = true',
        ),
        encoding='utf-8',
    )

    result = run_command(['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'], cwd=repo)
    assert result.returncode == 3


def test_run_requires_all_locked_runtime_packages_and_requests_api(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo)
    site_packages = Path(
        subprocess.run(
            [str(python), '-I', '-c', 'import site; print(site.getsitepackages()[0])'],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    shutil.rmtree(site_packages / 'idna')
    result = run_command(['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'], cwd=repo)
    assert result.returncode == 3

    (site_packages / 'idna').mkdir()
    (site_packages / 'idna' / '__init__.py').write_text(
        "__version__ = '3.19'\ndef encode(value): return b''\ndef decode(value): return ''\n",
        encoding='utf-8',
    )
    requests_module = site_packages / 'requests' / '__init__.py'
    requests_module.write_text("__version__ = '2.34.2'\n", encoding='utf-8')
    result = run_command(['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'], cwd=repo)
    assert result.returncode == 3


def test_install_default_is_offline_and_machine_readable(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = tmp_path / 'network-called'

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--json'],
        cwd=repo,
        env={'NETWORK_MARKER': str(marker), 'TMPDIR': str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    payload = json.loads(result.stdout)
    assert payload['ok'] is True
    assert payload['smokeTest'] is False
    assert payload['fullCheck'] is False
    assert payload['requirementsInstalled'] is False
    assert not marker.exists()


@pytest.mark.parametrize(
    ('entrypoint', 'arguments', 'expected_returncode'),
    (
        ('run.sh', ('--runtime-info',), 3),
        ('check.sh', ('--quiet',), 1),
        ('install.sh', ('--json',), 10),
    ),
)
def test_shell_entrypoints_reject_a_nonsticky_world_writable_source_parent(
    tmp_path,
    entrypoint,
    arguments,
    expected_returncode,
):
    replaceable_parent = tmp_path / 'replaceable-parent'
    replaceable_parent.mkdir(mode=0o777)
    replaceable_parent.chmod(0o777)
    repo = make_test_repo(replaceable_parent)

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments],
        cwd=repo,
    )

    assert result.returncode == expected_returncode
    if entrypoint == 'install.sh':
        payload = json.loads(result.stdout)
        assert payload['exitKind'] == 'install_error'
        assert payload['exitCode'] == 10
    else:
        assert 'source tree or its parent chain is unsafe' in result.stderr


@pytest.mark.skipif(os.geteuid() != 0, reason='creating a foreign-owned parent requires root')
@pytest.mark.parametrize(
    ('entrypoint', 'arguments', 'expected_returncode'),
    (
        ('run.sh', ('--runtime-info',), 3),
        ('check.sh', ('--quiet',), 1),
        ('install.sh', ('--json',), 10),
    ),
)
def test_shell_entrypoints_reject_a_foreign_owned_readonly_source_parent(
    tmp_path,
    entrypoint,
    arguments,
    expected_returncode,
):
    foreign_parent = tmp_path / 'foreign-parent'
    foreign_parent.mkdir(mode=0o755)
    repo = make_test_repo(foreign_parent)
    foreign_uid = next(uid for uid in (65534, 65533, 12345) if uid not in {0, os.geteuid()})
    os.chown(foreign_parent, foreign_uid, -1)
    foreign_parent.chmod(0o755)

    result = run_command(
        ['/bin/bash', '-p', f'scripts/{entrypoint}', *arguments],
        cwd=repo,
    )

    assert result.returncode == expected_returncode
    if entrypoint == 'install.sh':
        payload = json.loads(result.stdout)
        assert payload['exitKind'] == 'install_error'
        assert payload['exitCode'] == 10
    else:
        assert 'source tree or its parent chain is unsafe' in result.stderr


def test_check_rejects_checkout_replacement_after_runner_returns(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    runner = repo / 'scripts' / 'run.sh'
    source = runner.read_text(encoding='utf-8')
    source = source.replace(
        'BASE_DIR="$(cd -- "$ENTRYPOINT_DIR/.." && pwd -P)"',
        'BASE_DIR="$(cd -- "$ENTRYPOINT_DIR/.." && pwd -P)"\n'
        "trap 'mv -- \"$BASE_DIR\" \"${BASE_DIR}.replaced\"' EXIT",
        1,
    )
    runner.write_text(source, encoding='utf-8')

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--venv', '--quiet'],
        cwd=repo,
    )

    assert result.returncode == 1
    assert 'source tree or its parent chain changed during the check' in result.stderr
    assert not repo.exists()
    assert repo.with_name(f'{repo.name}.replaced').is_dir()


def test_check_rejects_python_source_replacement_after_runner_returns(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    runner = repo / 'scripts' / 'run.sh'
    source = runner.read_text(encoding='utf-8')
    source = source.replace(
        'BASE_DIR="$(cd -- "$ENTRYPOINT_DIR/.." && pwd -P)"',
        'BASE_DIR="$(cd -- "$ENTRYPOINT_DIR/.." && pwd -P)"\n'
        "trap 'echo \"raise SystemExit(97)\" >> "
        "\"$BASE_DIR/scripts/check_protocol.py\"' EXIT",
        1,
    )
    runner.write_text(source, encoding='utf-8')

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--venv', '--quiet'],
        cwd=repo,
    )

    assert result.returncode == 1
    assert 'source tree or its parent chain changed during the check' in result.stderr


def test_check_ignores_preexisting_bytecode_cache_and_does_not_reuse_it(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    cache = repo / 'scripts' / '__pycache__'
    cache.mkdir()
    (cache / 'client.cpython-311.pyc').write_bytes(b'not trusted bytecode')

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--venv', '--quiet'],
        cwd=repo,
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )

    assert result.returncode == 0, result.stderr
    assert (cache / 'client.cpython-311.pyc').read_bytes() == b'not trusted bytecode'


def test_install_uses_healthy_venv_without_system_python_on_path(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    fake_bin = make_path_without_python(tmp_path)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--json'],
        cwd=repo,
        env={'PATH': str(fake_bin)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['ok'] is True
    assert payload['mode'] == 'venv'


def test_install_full_check_passes_full_and_uses_private_result(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    record = tmp_path / 'selfcheck-args.json'
    marker = tmp_path / 'full-called'

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--full-check', '--json'],
        cwd=repo,
        env={
            'SELFCHECK_RECORD': str(record),
            'NETWORK_MARKER': str(marker),
            'SERPER_API_KEY': 'test-only-key-123',
            'TMPDIR': str(tmp_path),
        },
        timeout=INSTALL_ONLINE_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert json.loads(record.read_text(encoding='utf-8')) == ['--full', '--compact']
    result_path = Path(payload['selfcheckResultPath'])
    assert result_path.is_file()
    assert result_path.parent == Path('/tmp')
    assert stat.S_IMODE(result_path.stat().st_mode) == 0o600
    assert marker.read_text(encoding='utf-8') == 'full'
    result_path.unlink()


def test_install_online_precheck_accepts_plural_key_env(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = tmp_path / 'smoke-called'

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--smoke-test', '--json'],
        cwd=repo,
        env={
            'SERPER_API_KEYS': 'test-key-one-123,test-key-two-456',
            'NETWORK_MARKER': str(marker),
            'TMPDIR': str(tmp_path),
        },
        timeout=INSTALL_ONLINE_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['smokeTest'] is True
    assert marker.read_text(encoding='utf-8') == 'smoke'
    Path(payload['smokeTestResultPath']).unlink()


@pytest.mark.parametrize(
    ('flag', 'worker_script', 'result_key'),
    (
        ('--smoke-test', 'smoke_test.py', 'smokeTestResultPath'),
        ('--full-check', 'selfcheck.py', 'selfcheckResultPath'),
    ),
)
def test_install_online_worker_keeps_keys_but_result_validator_does_not(
    tmp_path,
    flag,
    worker_script,
    result_key,
):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    worker_record = tmp_path / 'worker-api-environment.json'
    validator_record = tmp_path / 'validator-api-environment.json'
    worker = repo / 'scripts' / worker_script
    worker_source = worker.read_text(encoding='utf-8')
    import_line = 'import json, os, pathlib\n' if worker_script == 'smoke_test.py' else 'import json, os, pathlib, sys\n'
    worker.write_text(
        worker_source.replace(
            import_line,
            import_line
            + f'pathlib.Path({str(worker_record)!r}).write_text('
            + 'json.dumps([os.environ.get("SERPER_API_KEY"), '
            + 'os.environ.get("SERPER_API_KEYS")]))\n',
            1,
        ),
        encoding='utf-8',
    )
    protocol = repo / 'scripts' / 'check_protocol.py'
    protocol.write_text(
        protocol.read_text(encoding='utf-8').replace(
            'def validate_result(payload, expected):\n',
            'def validate_result(payload, expected):\n'
            + f'    Path({str(validator_record)!r}).write_text('
            + 'json.dumps([os.environ.get("SERPER_API_KEY"), '
            + 'os.environ.get("SERPER_API_KEYS")]))\n',
            1,
        ),
        encoding='utf-8',
    )
    singular = 'test-only-singular-key-123'
    plural = 'test-only-plural-key-456,test-only-plural-key-789'

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', flag, '--json'],
        cwd=repo,
        env={'SERPER_API_KEY': singular, 'SERPER_API_KEYS': plural},
        timeout=INSTALL_ONLINE_TIMEOUT,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(worker_record.read_text(encoding='utf-8')) == [singular, plural]
    assert json.loads(validator_record.read_text(encoding='utf-8')) == [None, None]
    assert singular not in result.stdout and plural not in result.stdout
    assert singular not in result.stderr and plural not in result.stderr
    Path(json.loads(result.stdout)[result_key]).unlink()


@pytest.mark.parametrize(
    ('flag', 'target', 'expected_exit_code', 'expected_exit_kind', 'result_key'),
    (
        ('--smoke-test', 'smoke', 4, 'smoke_test_error', 'smokeTestResultPath'),
        ('--full-check', 'full', 5, 'selfcheck_error', 'selfcheckResultPath'),
    ),
)
@pytest.mark.parametrize('bad_mode', ('empty', 'object', 'schema'))
def test_install_rejects_invalid_online_result_protocol(
    tmp_path,
    flag,
    target,
    expected_exit_code,
    expected_exit_kind,
    result_key,
    bad_mode,
):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', flag, '--json'],
        cwd=repo,
        env={
            'SERPER_API_KEY': 'test-only-key-123',
            'CHECK_BAD_TARGET': target,
            'CHECK_BAD_MODE': bad_mode,
        },
        timeout=INSTALL_ONLINE_TIMEOUT,
    )

    assert result.returncode == expected_exit_code, result.stderr
    payload = json.loads(result.stdout)
    assert payload['ok'] is False
    assert payload['exitKind'] == expected_exit_kind
    assert payload['exitCode'] == expected_exit_code
    result_path = Path(payload[result_key])
    assert result_path.is_file()
    assert result_path.parent == Path('/tmp')
    assert stat.S_IMODE(result_path.stat().st_mode) == 0o600
    result_path.unlink()


def test_install_invalid_api_config_is_config_error_before_network(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = tmp_path / 'smoke-called'

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--smoke-test', '--json'],
        cwd=repo,
        env={
            'SERPER_API_KEY': '',
            'SERPER_API_KEYS': 'otherwise-valid-key-123',
            'NETWORK_MARKER': str(marker),
        },
        timeout=INSTALL_ONLINE_TIMEOUT,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload['exitKind'] == 'config_error'
    assert payload['exitCode'] == 2
    assert not marker.exists()


def test_install_text_reports_result_and_quiet_text_cleans_it(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = tmp_path / 'smoke-called'
    env = {
        'SERPER_API_KEYS': 'test-only-key-123',
        'NETWORK_MARKER': str(marker),
        'TMPDIR': str(tmp_path),
    }

    visible = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--smoke-test'],
        cwd=repo,
        env=env,
        timeout=INSTALL_ONLINE_TIMEOUT,
    )
    assert visible.returncode == 0, visible.stderr
    assert '[google-search] smoke result: ' in visible.stdout
    visible_path = Path(visible.stdout.split('smoke result: ', 1)[1].splitlines()[0])
    assert visible_path.parent == Path('/tmp')
    visible_path.unlink()

    quiet_record = tmp_path / 'quiet-result-path'
    quiet = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--smoke-test', '--quiet'],
        cwd=repo,
        env={**env, 'CHECK_RESULT_PATH_RECORD': str(quiet_record)},
        timeout=INSTALL_ONLINE_TIMEOUT,
    )
    assert quiet.returncode == 0, quiet.stderr
    assert quiet.stdout == ''
    quiet_result_path = Path(quiet_record.read_text(encoding='utf-8'))
    assert not quiet_result_path.exists()


@pytest.mark.parametrize(
    ('flag', 'target', 'kind', 'expected_exit_code'),
    (
        ('--smoke-test', 'smoke', 'smoke', 4),
        ('--full-check', 'full', 'selfcheck', 5),
    ),
)
def test_install_quiet_cleans_result_when_protocol_validation_fails(
    tmp_path,
    flag,
    target,
    kind,
    expected_exit_code,
):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    result_record = tmp_path / 'result-path'

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', flag, '--quiet'],
        cwd=repo,
        env={
            'SERPER_API_KEY': 'test-only-key-123',
            'CHECK_BAD_TARGET': target,
            'CHECK_BAD_MODE': 'schema',
            'CHECK_RESULT_PATH_RECORD': str(result_record),
        },
        timeout=INSTALL_ONLINE_TIMEOUT,
    )

    assert result.returncode == expected_exit_code, result.stderr
    assert result.stdout == ''
    result_path = Path(result_record.read_text(encoding='utf-8'))
    assert result_path.name.startswith(f'google-search-{kind}.')
    assert not result_path.exists()


@pytest.mark.parametrize(
    ('flag', 'worker_script', 'kind'),
    (
        ('--smoke-test', 'smoke_test.py', 'smoke'),
        ('--full-check', 'selfcheck.py', 'selfcheck'),
    ),
)
@pytest.mark.parametrize(
    ('install_signal', 'expected_exit_code'),
    (
        (signal.SIGHUP, 129),
        (signal.SIGINT, 130),
        (signal.SIGTERM, 143),
    ),
)
def test_install_quiet_signal_cleans_online_result(
    tmp_path,
    flag,
    worker_script,
    kind,
    install_signal,
    expected_exit_code,
):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    result_record = tmp_path / 'result-path'
    worker_pid_record = tmp_path / 'worker-pid'
    worker = repo / 'scripts' / worker_script
    worker.write_text(
        worker.read_text(encoding='utf-8').replace(
            'bad_mode = ',
            'import time\n'
            'pathlib.Path(os.environ["CHECK_WORKER_PID_RECORD"]).write_text(str(os.getpid()))\n'
            'time.sleep(60)\nbad_mode = ',
            1,
        ),
        encoding='utf-8',
    )

    clean_env = {
        name: value for name, value in os.environ.items()
        if name not in {'SERPER_API_KEY', 'SERPER_API_KEYS'}
    }
    clean_env.update({
        'SERPER_API_KEY': 'test-only-key-123',
        'CHECK_RESULT_PATH_RECORD': str(result_record),
        'CHECK_WORKER_PID_RECORD': str(worker_pid_record),
    })
    process = subprocess.Popen(
        ['/bin/bash', '-p', 'scripts/install.sh', flag, '--quiet'],
        cwd=repo,
        env=clean_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + INSTALL_ONLINE_TIMEOUT
        while (
            (not result_record.exists() or not worker_pid_record.exists())
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert result_record.exists() and worker_pid_record.exists()
        result_path = Path(result_record.read_text(encoding='utf-8'))
        worker_pid = int(worker_pid_record.read_text(encoding='utf-8'))
        worker_stat = Path(f'/proc/{worker_pid}/stat').read_text(encoding='utf-8')
        worker_start_time = worker_stat.rsplit(')', 1)[1].split()[19]
        os.kill(process.pid, install_signal)
        stdout, stderr = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)

    assert process.returncode == expected_exit_code, (stdout, stderr)
    assert result_path.name.startswith(f'google-search-{kind}.')
    assert not result_path.exists()
    worker_stat_path = Path(f'/proc/{worker_pid}/stat')
    if worker_stat_path.exists():
        current_stat = worker_stat_path.read_text(encoding='utf-8')
        assert current_stat.rsplit(')', 1)[1].split()[19] != worker_start_time


def test_discard_private_result_rejects_directories_and_unlinks_symlinks(tmp_path):
    install_script = (ROOT / 'scripts' / 'install.sh').read_text(encoding='utf-8')
    start = install_script.index('discard_private_result() {')
    end = install_script.index('\n}\n\ncleanup()', start) + 3
    helper = install_script[start:end]
    safe_tmp = tmp_path / 'safe-tmp'
    safe_tmp.mkdir()
    victim = tmp_path / 'victim'
    victim.write_text('unchanged', encoding='utf-8')
    symlink = safe_tmp / 'google-search-smoke.A1b2C3d4'
    symlink.symlink_to(victim)
    directory = safe_tmp / 'google-search-smoke.D1r2C3t4'
    directory.mkdir()
    outside = tmp_path / 'google-search-smoke.O1u2T3s4'
    outside.write_text('outside', encoding='utf-8')
    traversal_dir = safe_tmp / 'google-search-smoke.R0u7e123'
    traversal_dir.mkdir()
    traversal = f'{traversal_dir}/../../victim'
    noncanonical = f'{safe_tmp}/./google-search-smoke.N0n1C2n3'
    double_slash = f'{safe_tmp}//google-search-smoke.D0u8L3s4'
    short_suffix = safe_tmp / 'google-search-smoke.short'
    unsafe_suffix = safe_tmp / 'google-search-smoke.Bad_Name'
    wrong_kind = safe_tmp / 'google-search-full.A1b2C3d4'

    command = (
        f'SAFE_TMP_DIR={str(safe_tmp)!r}\n{helper}\n'
        'discard_private_result "$1" smoke\n'
    )
    removed = subprocess.run(
        ['/bin/bash', '-c', command, '_', str(symlink)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert removed.returncode == 0, removed.stderr
    assert not symlink.exists() and not symlink.is_symlink()
    assert victim.read_text(encoding='utf-8') == 'unchanged'

    for rejected in (
        directory,
        outside,
        traversal,
        noncanonical,
        double_slash,
        short_suffix,
        unsafe_suffix,
        wrong_kind,
        '',
    ):
        result = subprocess.run(
            ['/bin/bash', '-c', command, '_', str(rejected)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0, rejected
    assert directory.exists()
    assert outside.exists()
    assert victim.read_text(encoding='utf-8') == 'unchanged'


def test_private_result_cleanup_failure_maps_to_install_error(tmp_path):
    install_script = (ROOT / 'scripts' / 'install.sh').read_text(encoding='utf-8')
    start = install_script.index('discard_private_result() {')
    end = install_script.index('\n}\n\nactive_process_is_shell_job()', start) + 3
    helpers = install_script[start:end]
    safe_tmp = tmp_path / 'safe-tmp'
    safe_tmp.mkdir()
    blocked = safe_tmp / 'google-search-smoke.B1o2C3k4'
    blocked.mkdir()
    script = (
        f'SAFE_TMP_DIR={str(safe_tmp)!r}\n'
        'fail() { exit "$2"; }\n'
        f'{helpers}\n'
        'discard_private_result_or_fail "$1" smoke\n'
    )

    result = subprocess.run(
        ['/bin/bash', '-c', script, '_', str(blocked)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 10
    assert blocked.is_dir()


def test_online_signal_adopts_only_the_new_shell_job_and_avoids_stale_pid_kills():
    install_script = (ROOT / 'scripts' / 'install.sh').read_text(encoding='utf-8')
    active_start = install_script.index('active_process_is_shell_job() {')
    active_end = install_script.index('\n}\n\ncleanup()', active_start) + 3
    active_helpers = install_script[active_start:active_end]
    record_start = install_script.index('record_online_signal() {')
    record_end = install_script.index('\n}\nset_install_signal_traps()', record_start) + 3
    record_helper = install_script[record_start:record_end]
    script = (
        'set -euo pipefail\n'
        f'{active_helpers}\n{record_helper}\n'
        'ACTIVE_ONLINE_WORKER_PID=\n'
        'ONLINE_WORKER_PREVIOUS_PID=\n'
        'ONLINE_WORKER_LAUNCHING=1\n'
        'PENDING_ONLINE_SIGNAL_STATUS=\n'
        'survivor=\n'
        'cleanup_probe() {\n'
        '  [ -z "$survivor" ] || kill -s TERM "$survivor" 2>/dev/null || true\n'
        '  [ -z "$survivor" ] || wait "$survivor" 2>/dev/null || true\n'
        '}\n'
        'trap cleanup_probe EXIT\n'
        'sleep 60 & spawned=$!\n'
        'record_online_signal 143\n'
        '[ "$ACTIVE_ONLINE_WORKER_PID" = "$spawned" ]\n'
        '[ "$PENDING_ONLINE_SIGNAL_STATUS" = 143 ]\n'
        '! active_process_is_shell_job "$spawned"\n'
        'sleep 60 & survivor=$!\n'
        'terminate_active_process "$spawned"\n'
        'active_process_is_shell_job "$survivor"\n'
        'printf "ONLINE_SIGNAL_JOB_IDENTITY_OK\\n"\n'
    )

    result = subprocess.run(
        ['/bin/bash', '-c', script],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == 'ONLINE_SIGNAL_JOB_IDENTITY_OK\n'


def test_install_helper_signal_adopts_the_job_before_pid_assignment():
    install_script = (ROOT / 'scripts' / 'install.sh').read_text(encoding='utf-8')
    helper_start = install_script.index('run_locked_install_helper() {')
    helper_end = install_script.index('\n}\n\njson_python()', helper_start) + 3
    locked_helper = install_script[helper_start:helper_end]
    launching = locked_helper.index('INSTALL_HELPER_LAUNCHING=1')
    signal_traps = locked_helper.index('set_install_helper_signal_traps')
    spawn = locked_helper.index('2>>"$COMMAND_LOG" &')
    pid_assignment = locked_helper.index('ACTIVE_INSTALL_HELPER_PID=$!')
    launch_complete = locked_helper.index('INSTALL_HELPER_LAUNCHING=0', pid_assignment)
    assert launching < signal_traps < spawn < pid_assignment < launch_complete

    active_start = install_script.index('active_process_is_shell_job() {')
    active_end = install_script.index('\n}\n\ncleanup()', active_start) + 3
    active_helpers = install_script[active_start:active_end]
    record_start = install_script.index('record_install_helper_signal() {')
    record_end = install_script.index('\n}\nrecord_online_signal()', record_start) + 3
    record_helper = install_script[record_start:record_end]
    script = (
        'set -euo pipefail\n'
        f'{active_helpers}\n{record_helper}\n'
        'ACTIVE_INSTALL_HELPER_PID=\n'
        'INSTALL_HELPER_PREVIOUS_PID=\n'
        'INSTALL_HELPER_LAUNCHING=1\n'
        'PENDING_INSTALL_SIGNAL_STATUS=\n'
        'sleep 60 & spawned=$!\n'
        'record_install_helper_signal 143\n'
        '[ "$ACTIVE_INSTALL_HELPER_PID" = "$spawned" ]\n'
        '[ "$PENDING_INSTALL_SIGNAL_STATUS" = 143 ]\n'
        '! active_process_is_shell_job "$spawned"\n'
        'printf "INSTALL_HELPER_SIGNAL_JOB_ADOPTED_OK\\n"\n'
    )

    result = subprocess.run(
        ['/bin/bash', '-c', script],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == 'INSTALL_HELPER_SIGNAL_JOB_ADOPTED_OK\n'


def test_install_securely_saves_json_with_mode_0600(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    save_path = tmp_path / 'results' / 'install.json'

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--save-json', str(save_path), '--quiet'],
        cwd=repo,
        env={'TMPDIR': str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == json.loads(save_path.read_text(encoding='utf-8'))
    assert stat.S_IMODE(save_path.stat().st_mode) == 0o600


@pytest.mark.parametrize('unsafe_character', ['\x85', '\u061c', '\u2028', '\u2029', '\u202e', '\u2066'])
def test_install_save_json_rejects_terminal_unsafe_path_characters(tmp_path, unsafe_character):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    target = os.fspath(tmp_path) + f'/install{unsafe_character}.json'

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--save-json', target, '--json'],
        cwd=repo,
    )

    assert result.returncode == 10
    payload = json.loads(result.stdout)
    assert payload['ok'] is False
    assert payload['exitKind'] == 'install_error'
    assert unsafe_character not in result.stdout
    assert not Path(target).exists()


def test_install_rejects_unicode_line_separator_in_runtime_path_without_reflecting_it(tmp_path):
    parent = tmp_path / 'line\u2028separator'
    parent.mkdir()
    repo = make_test_repo(parent)
    create_stub_runtime(repo)

    result = run_command(['/bin/bash', '-p', 'scripts/install.sh', '--json'], cwd=repo)

    assert result.returncode == 3, result.stderr
    assert '\u2028' not in result.stdout
    assert str(parent).replace('\u2028', '\\u2028') not in result.stdout
    payload = json.loads(result.stdout)
    assert payload['ok'] is False
    assert payload['exitKind'] == 'dependency_error'
    assert payload['exitCode'] == 3
    assert payload['python'] == ''


def test_install_relative_saved_json_path_reports_canonical_target(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    expected = repo / 'output' / 'nested' / 'install.json'

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--save-json', 'nested/install.json', '--quiet'],
        cwd=repo,
        env={'TMPDIR': str(tmp_path / 'untrusted-tmp')},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['savedJsonPath'] == str(expected.resolve())
    assert json.loads(expected.read_text(encoding='utf-8')) == payload


def test_install_save_json_rejects_an_option_as_its_path(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--save-json', '--quiet', '--json'],
        cwd=repo,
    )

    assert result.returncode == 2
    assert result.stderr == ''
    payload = json.loads(result.stdout)
    assert payload['exitKind'] == 'config_error'
    assert payload['exitCode'] == 2
    assert not (repo / 'output' / '--quiet').exists()


def test_install_save_failure_exit_matches_json(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--save-json', '/proc/google-search-install.json'],
        cwd=repo,
    )
    assert result.returncode == 10
    assert result.stderr == ''
    payload = json.loads(result.stdout)
    assert payload['ok'] is False
    assert payload['exitKind'] == 'install_error'
    assert payload['exitCode'] == 10


def test_install_rejects_empty_save_path_before_online_smoke(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = tmp_path / 'network-called'

    result = run_command(
        [
            '/bin/bash', '-p', 'scripts/install.sh', '--smoke-test',
            '--save-json', '',
        ],
        cwd=repo,
        env={'NETWORK_MARKER': str(marker)},
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)['exitKind'] == 'config_error'
    assert not marker.exists()


def test_install_preflights_save_path_before_dependency_install_or_smoke(tmp_path):
    repo = make_test_repo(tmp_path)
    marker = tmp_path / 'network-called'

    result = run_command(
        [
            '/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies',
            '--smoke-test', '--save-json', '/proc/google-search-install.json',
        ],
        cwd=repo,
        env={'NETWORK_MARKER': str(marker)},
    )

    assert result.returncode == 10
    assert json.loads(result.stdout)['exitKind'] == 'install_error'
    assert not marker.exists()
    assert not (repo / '.venv').exists()
    assert not (repo / '.venv.install.lock').exists()
    assert list(repo.glob('.venv-build.*')) == []
    assert list(repo.glob('.venv-bootstrap.*')) == []


def test_install_checks_secure_io_source_before_importing_it(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = tmp_path / 'unsafe-secure-io-imported'
    external = tmp_path / 'external'
    external.mkdir()
    malicious = external / 'secure_io.py'
    malicious.write_text(
        'from pathlib import Path\n'
        f'Path({str(marker)!r}).write_text("executed", encoding="utf-8")\n'
        'def _resolve_target(raw):\n'
        f'    return Path({str(tmp_path / "result.json")!r}), None\n',
        encoding='utf-8',
    )
    secure_io = repo / 'scripts' / 'secure_io.py'
    secure_io.unlink()
    secure_io.symlink_to(malicious)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--save-json', str(tmp_path / 'result.json')],
        cwd=repo,
    )

    assert result.returncode == 10
    assert json.loads(result.stdout)['exitKind'] == 'install_error'
    assert not marker.exists()


def test_install_ignores_untrusted_path_python_for_json_error(tmp_path):
    repo = make_test_repo(tmp_path)
    fake_bin = make_path_without_python(tmp_path)
    marker = tmp_path / 'untrusted-python-ran'
    fake_python = fake_bin / 'python3'
    fake_python.write_text(f'#!/bin/sh\ntouch {marker}\n', encoding='utf-8')
    fake_python.chmod(0o777)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--unknown', '--json'],
        cwd=repo,
        env={'PATH': str(fake_bin)},
    )
    assert result.returncode == 2
    assert result.stderr == ''
    payload = json.loads(result.stdout)
    assert payload['exitKind'] == 'config_error'
    assert payload['exitCode'] == 2
    assert not marker.exists()


@pytest.mark.parametrize('entrypoint', ('install.sh', 'check.sh'))
@pytest.mark.parametrize('token', ('--bad\x1b[31moption', '--bad\u0085option', '--bad\u202eoption'))
def test_shell_unknown_options_do_not_echo_terminal_controls(tmp_path, entrypoint, token):
    repo = make_test_repo(tmp_path)
    result = run_command(['/bin/bash', '-p', f'scripts/{entrypoint}', token], cwd=repo)

    assert result.returncode in {1, 2}
    assert token not in result.stdout
    assert token not in result.stderr
    assert '\x1b' not in result.stdout + result.stderr
    assert '\u0085' not in result.stdout + result.stderr
    assert '\u202e' not in result.stdout + result.stderr


def test_install_rejects_system_dependency_install_as_pure_json(tmp_path):
    repo = make_test_repo(tmp_path)
    for flag, dev_expected in (
        ('--install-dependencies', False),
        ('--install-dev-dependencies', True),
    ):
        result = run_command(
            ['/bin/bash', '-p', 'scripts/install.sh', '--system', flag, '--json'],
            cwd=repo,
        )
        assert result.returncode == 2
        assert result.stderr == ''
        payload = json.loads(result.stdout)
        assert payload['exitKind'] == 'config_error'
        assert payload['exitCode'] == 2
        assert payload['installDevDependencies'] is dev_expected


def test_install_dependency_failure_does_not_modify_existing_venv(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = repo / '.venv' / 'existing-marker'
    marker.write_text('preserve me', encoding='utf-8')
    original_inode = (repo / '.venv').stat().st_ino
    missing_wheel = (repo / 'missing-package-1.0-py3-none-any.whl').as_uri()
    (repo / 'requirements.txt').write_text(
        f'missing-package @ {missing_wheel} --hash=sha256:{"0" * 64}\n',
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        timeout=60,
    )
    assert result.returncode == 3, result.stderr
    payload = json.loads(result.stdout)
    assert payload['exitKind'] == 'dependency_error'
    assert marker.read_text(encoding='utf-8') == 'preserve me'
    assert (repo / '.venv').stat().st_ino == original_inode
    assert list(repo.glob('.venv-build.*')) == []
    lock = repo / '.venv.install.lock'
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert lock.stat().st_nlink == 1


def test_install_rejects_an_untrusted_dependency_lock_before_staging(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    requirements = repo / 'requirements.txt'
    requirements.chmod(0o666)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
    )

    assert result.returncode == 10
    payload = json.loads(result.stdout)
    assert payload['exitKind'] == 'install_error'
    assert list(repo.glob('.venv-build.*')) == []
    assert list(repo.glob('.venv-bootstrap.*')) == []


def test_install_detects_dependency_lock_inode_replacement_before_publish(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    old_marker = repo / '.venv' / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    original_inode = (repo / '.venv').stat().st_ino
    requirements = repo / 'requirements.txt'
    preamble = (
        "if Path(sys.prefix).name.startswith('.venv-build.'):\n"
        f"    lock = Path({str(requirements)!r})\n"
        "    replacement = lock.with_name('requirements.replacement')\n"
        "    replacement.write_bytes(lock.read_bytes())\n"
        "    os.replace(replacement, lock)"
    )
    write_local_runtime_lock(
        repo,
        module_overrides={'requests': locked_requests_module(preamble)},
    )
    locked_inode = requirements.stat().st_ino

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        timeout=90,
    )

    assert result.returncode == 3, result.stderr
    payload = json.loads(result.stdout)
    assert payload['exitKind'] == 'dependency_error'
    assert 'lock changed' in payload['error']
    assert requirements.stat().st_ino != locked_inode
    assert (repo / '.venv').stat().st_ino == original_inode
    assert old_marker.read_text(encoding='utf-8') == 'old'
    assert list(repo.glob('.venv-build.*')) == []
    assert list(repo.glob('.venv-bootstrap.*')) == []


def test_install_builds_fresh_candidate_and_atomically_replaces_venv(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    old_marker = repo / '.venv' / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    original_inode = (repo / '.venv').stat().st_ino
    write_local_runtime_lock(repo)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['requirementsInstalled'] is True
    assert (repo / '.venv').stat().st_ino != original_inode
    assert not old_marker.exists()
    assert list(repo.glob('.venv-build.*')) == []
    selected = run_command(['/bin/bash', '-p', 'scripts/run.sh', '--venv', '--runtime-info'], cwd=repo)
    assert selected.returncode == 0, selected.stderr
    package_probe = run_command(
        [
            str(repo / '.venv' / 'bin' / 'python'),
            '-I',
            '-c',
            (
                'import importlib.metadata, importlib.util, json, site, pathlib; '
                'names={d.metadata["Name"].lower() for d in importlib.metadata.distributions()}; '
                'paths=[pathlib.Path(p) for p in site.getsitepackages()]; '
                'print(json.dumps({"pip": "pip" in names, "setuptools": "setuptools" in names, '
                '"pipModule": importlib.util.find_spec("pip") is not None, '
                '"pth": any(list(p.glob("*.pth")) for p in paths)}))'
            ),
        ],
        cwd=repo,
    )
    assert package_probe.returncode == 0, package_probe.stderr
    assert json.loads(package_probe.stdout) == {
        'pip': False,
        'setuptools': False,
        'pipModule': False,
        'pth': False,
    }


def test_install_repairs_a_damaged_owned_venv_after_candidate_validation(tmp_path):
    repo = make_test_repo(tmp_path)
    damaged = repo / '.venv'
    nested = damaged / 'damaged-state'
    nested.mkdir(parents=True, mode=0o700)
    marker = nested / 'old-marker'
    marker.write_text('old', encoding='utf-8')
    original_inode = damaged.stat().st_ino
    temp_record = tmp_path / 'candidate-temp-environment'
    preamble = (
        f'Path({str(temp_record)!r}).write_text('
        "'|'.join(os.environ.get(name, '') for name in ('TMPDIR', 'TMP', 'TEMP')), "
        "encoding='utf-8')"
    )
    write_local_runtime_lock(
        repo,
        module_overrides={'requests': locked_requests_module(preamble)},
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        env={
            'TMPDIR': str(tmp_path / 'untrusted-tmpdir'),
            'TMP': str(tmp_path / 'untrusted-tmp'),
            'TEMP': str(tmp_path / 'untrusted-temp'),
        },
        timeout=90,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['ok'] is True
    assert payload['requirementsInstalled'] is True
    assert damaged.stat().st_ino != original_inode
    assert not marker.exists()
    assert temp_record.read_text(encoding='utf-8') == '/tmp|/tmp|/tmp'
    assert list(repo.glob('.venv-build.*')) == []


def test_install_rejects_pth_before_candidate_startup_code_executes(tmp_path):
    repo = make_test_repo(tmp_path)
    damaged = repo / '.venv'
    damaged.mkdir(mode=0o700)
    old_marker = damaged / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    startup_marker = tmp_path / 'startup-hook-executed'
    pth_source = (
        'import pathlib; '
        f'pathlib.Path({str(startup_marker)!r}).write_text("executed", encoding="utf-8")\n'
    )
    write_local_runtime_lock(
        repo,
        extra_files={'requests': {'untrusted-startup.pth': pth_source}},
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        timeout=90,
    )

    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)['exitKind'] == 'dependency_error'
    assert old_marker.read_text(encoding='utf-8') == 'old'
    assert not startup_marker.exists()
    assert list(repo.glob('.venv-build.*')) == []


def test_install_rolls_back_if_candidate_import_creates_a_startup_hook(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    old_marker = repo / '.venv' / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    original_inode = (repo / '.venv').stat().st_ino
    startup_marker = tmp_path / 'late-startup-hook-executed'
    startup_code = (
        'import pathlib; '
        f'pathlib.Path({str(startup_marker)!r}).write_text("executed")\n'
    )
    preamble = (
        "if Path(sys.prefix).name == '.venv':\n"
        "    site_packages = Path(__file__).resolve().parent.parent\n"
        f"    (site_packages / 'late-startup.pth').write_text({startup_code!r}, encoding='utf-8')"
    )
    write_local_runtime_lock(
        repo,
        module_overrides={'requests': locked_requests_module(preamble)},
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        timeout=90,
    )

    assert result.returncode == 3, result.stderr
    payload = json.loads(result.stdout)
    assert payload['exitKind'] == 'dependency_error'
    assert 'original .venv was restored' in payload['error']
    assert (repo / '.venv').stat().st_ino == original_inode
    assert old_marker.read_text(encoding='utf-8') == 'old'
    assert not startup_marker.exists()
    assert list(repo.glob('.venv-build.*')) == []


def test_install_rechecks_named_lock_inode_immediately_before_publish(tmp_path):
    repo = make_test_repo(tmp_path)
    damaged = repo / '.venv'
    damaged.mkdir(mode=0o700)
    old_marker = damaged / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    original_inode = damaged.stat().st_ino
    import_marker = tmp_path / 'candidate-import-started'
    preamble = (
        f'Path({str(import_marker)!r}).write_text("started", encoding="utf-8")\n'
        'time.sleep(2)'
    )
    write_local_runtime_lock(
        repo,
        module_overrides={'requests': locked_requests_module(preamble)},
    )

    process = subprocess.Popen(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 60
        while not import_marker.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.01)
        assert import_marker.exists(), process.communicate(timeout=5)
        lock = repo / '.venv.install.lock'
        held_inode = lock.stat().st_ino
        lock.unlink()
        replacement = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(replacement)
        assert lock.stat().st_ino != held_inode
        stdout, stderr = process.communicate(timeout=90)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 3, stderr
    assert json.loads(stdout)['exitKind'] == 'dependency_error'
    assert damaged.stat().st_ino == original_inode
    assert old_marker.read_text(encoding='utf-8') == 'old'
    assert list(repo.glob('.venv-build.*')) == []


def test_install_rolls_back_when_candidate_fails_at_live_path(tmp_path):
    repo = make_test_repo(tmp_path)
    damaged = repo / '.venv'
    damaged.mkdir(mode=0o700)
    old_marker = damaged / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    original_inode = damaged.stat().st_ino
    preamble = (
        "if Path(sys.prefix).name == '.venv':\n"
        "    raise RuntimeError('injected live-path failure')"
    )
    write_local_runtime_lock(
        repo,
        module_overrides={'requests': locked_requests_module(preamble)},
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        timeout=90,
    )

    assert result.returncode == 3, result.stderr
    payload = json.loads(result.stdout)
    assert payload['exitKind'] == 'dependency_error'
    assert 'original .venv was restored' in payload['error']
    assert damaged.stat().st_ino == original_inode
    assert old_marker.read_text(encoding='utf-8') == 'old'
    assert list(repo.glob('.venv-build.*')) == []


def test_install_rejects_candidate_content_mutation_during_package_probe(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    old_marker = repo / '.venv' / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    original_inode = (repo / '.venv').stat().st_ino
    preamble = (
        "if Path(sys.prefix).name.startswith('.venv-build.'):\n"
        "    payload = Path(__file__).with_name('payload.txt')\n"
        "    if payload.read_bytes() == b'AAAA':\n"
        "        payload.write_bytes(b'BBBB')"
    )
    write_local_runtime_lock(
        repo,
        module_overrides={'requests': locked_requests_module(preamble)},
        extra_files={'requests': {'requests/payload.txt': b'AAAA'}},
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        timeout=90,
    )

    assert result.returncode == 3, result.stderr
    payload = json.loads(result.stdout)
    assert payload['exitKind'] == 'dependency_error'
    assert 'changed during package and API validation' in payload['error']
    assert (repo / '.venv').stat().st_ino == original_inode
    assert old_marker.read_text(encoding='utf-8') == 'old'
    assert list(repo.glob('.venv-build.*')) == []


def test_install_candidate_cannot_release_the_parent_transaction_lock(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = tmp_path / 'candidate-lock-probe'
    preamble = (
        "if Path(sys.prefix).name.startswith('.venv-build.'):\n"
        '    import fcntl\n'
        f'    lock_metadata = os.stat({str(repo / ".venv.install.lock")!r})\n'
        '    inherited = False\n'
        '    for descriptor in range(3, 256):\n'
        '        try:\n'
        '            metadata = os.fstat(descriptor)\n'
        '        except OSError:\n'
        '            continue\n'
        '        if (metadata.st_dev, metadata.st_ino) == (lock_metadata.st_dev, lock_metadata.st_ino):\n'
        '            inherited = True\n'
        '            fcntl.flock(descriptor, fcntl.LOCK_UN)\n'
        f'    Path({str(marker)!r}).write_text(str(inherited), encoding="utf-8")\n'
        '    time.sleep(2)'
    )
    write_local_runtime_lock(
        repo,
        module_overrides={'requests': locked_requests_module(preamble)},
    )

    process = subprocess.Popen(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 60
        while not marker.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.01)
        assert marker.exists(), process.communicate(timeout=5)
        assert marker.read_text(encoding='utf-8') == 'False'

        descriptor = os.open(repo / '.venv.install.lock', os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        stdout, stderr = process.communicate(timeout=90)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 0, stderr
    assert json.loads(stdout)['requirementsInstalled'] is True


def test_install_offline_dependency_processes_do_not_receive_api_keys(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    locked_install_marker = tmp_path / 'locked-install-api-environment'
    candidate_marker = tmp_path / 'candidate-api-environment'
    locked_installer = repo / 'scripts' / 'locked_install.py'
    locked_installer.write_text(
        locked_installer.read_text(encoding='utf-8').replace(
            'MAX_LOCK_BYTES = 4 * 1024 * 1024\n',
            f'Path({str(locked_install_marker)!r}).write_text('
            "repr((os.environ.get('SERPER_API_KEY'), os.environ.get('SERPER_API_KEYS'))), "
            "encoding='utf-8')\n"
            'MAX_LOCK_BYTES = 4 * 1024 * 1024\n',
            1,
        ),
        encoding='utf-8',
    )
    preamble = (
        "if Path(sys.prefix).name.startswith('.venv-build.') or Path(sys.prefix).name == '.venv':\n"
        f'    Path({str(candidate_marker)!r}).write_text('
        "repr((os.environ.get('SERPER_API_KEY'), os.environ.get('SERPER_API_KEYS'))), "
        "encoding='utf-8')"
    )
    write_local_runtime_lock(
        repo,
        module_overrides={'requests': locked_requests_module(preamble)},
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        env={
            'SERPER_API_KEY': 'must-not-reach-offline-child',
            'SERPER_API_KEYS': 'must-not-reach-offline-children',
        },
        timeout=90,
    )

    assert result.returncode == 0, result.stderr
    assert locked_install_marker.read_text(encoding='utf-8') == '(None, None)'
    assert candidate_marker.read_text(encoding='utf-8') == '(None, None)'


def test_install_rejects_transaction_helper_replacement_during_candidate_validation(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    old_marker = repo / '.venv' / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    original_inode = (repo / '.venv').stat().st_ino
    marker = tmp_path / 'candidate-validation-started'
    preamble = (
        "if Path(sys.prefix).name.startswith('.venv-build.'):\n"
        f'    Path({str(marker)!r}).write_text("started", encoding="utf-8")\n'
        '    time.sleep(2)'
    )
    write_local_runtime_lock(
        repo,
        module_overrides={'requests': locked_requests_module(preamble)},
    )

    process = subprocess.Popen(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 60
        while not marker.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.01)
        assert marker.exists(), process.communicate(timeout=5)
        helper = repo / 'scripts' / 'venv_transaction.py'
        helper.write_text(helper.read_text(encoding='utf-8') + '\n# replaced during install\n',
                          encoding='utf-8')
        stdout, stderr = process.communicate(timeout=90)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 10, stderr
    assert json.loads(stdout)['exitKind'] == 'install_error'
    assert (repo / '.venv').stat().st_ino == original_inode
    assert old_marker.read_text(encoding='utf-8') == 'old'
    assert list(repo.glob('.venv-build.*')) == []


def test_install_rolls_back_when_lock_changes_after_atomic_exchange(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    old_marker = repo / '.venv' / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    original_inode = (repo / '.venv').stat().st_ino
    live_marker = tmp_path / 'live-validation-started'
    preamble = (
        "if Path(sys.prefix).name == '.venv':\n"
        f'    Path({str(live_marker)!r}).write_text("started", encoding="utf-8")\n'
        '    time.sleep(2)'
    )
    write_local_runtime_lock(
        repo,
        module_overrides={'requests': locked_requests_module(preamble)},
    )

    process = subprocess.Popen(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 60
        while not live_marker.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.01)
        assert live_marker.exists(), process.communicate(timeout=5)
        lock = repo / 'requirements.txt'
        replacement = repo / 'requirements.replacement'
        replacement.write_bytes(lock.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, lock)
        stdout, stderr = process.communicate(timeout=90)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 3, stderr
    payload = json.loads(stdout)
    assert payload['exitKind'] == 'dependency_error'
    assert 'lock changed after venv publication' in payload['error']
    assert (repo / '.venv').stat().st_ino == original_inode
    assert old_marker.read_text(encoding='utf-8') == 'old'
    assert list(repo.glob('.venv-build.*')) == []


def test_install_recovers_a_committed_exchange_after_helper_acknowledgement_failure(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    old_marker = repo / '.venv' / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    original_inode = (repo / '.venv').stat().st_ino
    write_local_runtime_lock(repo)
    helper = repo / 'scripts' / 'venv_transaction.py'
    helper.write_text(
        helper.read_text(encoding='utf-8').replace(
            "        installed = os.stat('.venv', dir_fd=directory_fd, follow_symlinks=False)\n",
            "        raise RuntimeError('injected acknowledgement failure')\n"
            "        installed = os.stat('.venv', dir_fd=directory_fd, follow_symlinks=False)\n",
            1,
        ),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        timeout=90,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['requirementsInstalled'] is True
    assert (repo / '.venv').stat().st_ino != original_inode
    assert not old_marker.exists()
    assert list(repo.glob('.venv-build.*')) == []


def test_install_refuses_staged_path_replacement_after_verified_cleanup_sentinel(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    old_marker = repo / '.venv' / 'old-marker'
    old_marker.write_text('old', encoding='utf-8')
    write_local_runtime_lock(repo)
    cleanup_marker = tmp_path / 'staged-cleanup-verified'
    installer = repo / 'scripts' / 'install.sh'
    installer.write_text(
        installer.read_text(encoding='utf-8').replace(
            '    staged_id="$(stat -c \'%d:%i\' -- "$TEMP_VENV_DIR" 2>/dev/null)" || staged_id="absent"\n',
            f'    : > {str(cleanup_marker)!r}\n'
            '    /bin/sleep 2\n'
            '    staged_id="$(stat -c \'%d:%i\' -- "$TEMP_VENV_DIR" 2>/dev/null)" || staged_id="absent"\n',
            1,
        ),
        encoding='utf-8',
    )

    process = subprocess.Popen(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    replacement = None
    preserved = repo / '.preserved-original-venv'
    try:
        deadline = time.monotonic() + 60
        while not cleanup_marker.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.01)
        assert cleanup_marker.exists(), process.communicate(timeout=5)
        staged = next(path for path in repo.glob('.venv-build.*') if (path / 'old-marker').exists())
        staged.rename(preserved)
        staged.mkdir(mode=0o700)
        replacement = staged / 'replacement-marker'
        replacement.write_text('do not remove', encoding='utf-8')
        stdout, stderr = process.communicate(timeout=90)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 10, stderr
    payload = json.loads(stdout)
    assert payload['exitKind'] == 'install_error'
    assert 'changed post-publication recovery path' in payload['error']
    assert replacement is not None and replacement.read_text(encoding='utf-8') == 'do not remove'
    assert (preserved / 'old-marker').read_text(encoding='utf-8') == 'old'
    assert not (repo / '.venv' / 'old-marker').exists()


def test_install_reports_warning_when_old_venv_cleanup_fails_after_commit():
    if os.geteuid() != 0 or shutil.which('runuser') is None:
        pytest.skip('requires root and runuser to exercise a post-commit ownership failure')
    try:
        account = pwd.getpwnam('www')
    except KeyError:
        pytest.skip('requires an unprivileged www account')

    raw_root = tempfile.mkdtemp(prefix='google-search-cleanup-warning-', dir='/tmp')
    root = Path(raw_root)
    staged = None
    process = None
    try:
        repo = make_test_repo(root)
        damaged = repo / '.venv'
        damaged.mkdir(mode=0o700)
        old_marker = damaged / 'old-marker'
        old_marker.write_text('old', encoding='utf-8')
        live_marker = root / 'live-validation-started'
        preamble = (
            "if Path(sys.prefix).name == '.venv':\n"
            f'    Path({str(live_marker)!r}).write_text("started", encoding="utf-8")\n'
            '    time.sleep(2)'
        )
        write_local_runtime_lock(
            repo,
            module_overrides={'requests': locked_requests_module(preamble)},
        )

        for path in root.rglob('*'):
            os.chown(path, account.pw_uid, account.pw_gid, follow_symlinks=False)
        os.chown(root, account.pw_uid, account.pw_gid)
        os.chown(repo, account.pw_uid, account.pw_gid)

        process = subprocess.Popen(
            [
                'runuser',
                '-u',
                account.pw_name,
                '--',
                '/bin/bash',
                '-p',
                'scripts/install.sh',
                '--install-dependencies',
                '--json',
            ],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 60
        while not live_marker.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.01)
        assert live_marker.exists(), process.communicate(timeout=5)

        deadline = time.monotonic() + 5
        while staged is None and time.monotonic() < deadline:
            staged = next(
                (path for path in repo.glob('.venv-build.*') if (path / 'old-marker').exists()),
                None,
            )
            if staged is None:
                time.sleep(0.01)
        assert staged is not None
        os.chown(staged, 0, 0)
        staged.chmod(0o500)

        stdout, stderr = process.communicate(timeout=90)
        assert process.returncode == 0, stderr
        payload = json.loads(stdout)
        assert payload['ok'] is True
        assert payload['postCommitWarning'] == (
            f'the new venv is active, but the old venv remains at {staged}'
        )
        assert staged.exists()
        assert not (repo / '.venv' / 'old-marker').exists()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
        if staged is not None and staged.exists():
            staged.chmod(0o700)
        shutil.rmtree(root, ignore_errors=True)


def test_install_ignores_noop_python_from_inherited_path(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    fake_bin = make_path_without_python(tmp_path)
    marker = tmp_path / 'untrusted-python-ran'
    fake_python = fake_bin / 'python3'
    fake_python.write_text(f'#!/bin/sh\ntouch {marker}\nexit 0\n', encoding='utf-8')
    fake_python.chmod(0o700)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--json'],
        cwd=repo,
        env={'PATH': str(fake_bin)},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ok'] is True
    assert not marker.exists()


def test_install_rejects_unsafe_transaction_lock(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    lock_target = repo / 'lock-target'
    lock_target.write_text('', encoding='utf-8')
    lock_target.chmod(0o600)
    (repo / '.venv.install.lock').symlink_to(lock_target)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies'],
        cwd=repo,
    )
    assert result.returncode == 3
    assert list(repo.glob('.venv-build.*')) == []


def test_install_rejects_fifo_dependency_lock_without_blocking(tmp_path):
    repo = make_test_repo(tmp_path)
    lock = repo / 'requirements.txt'
    lock.unlink()
    os.mkfifo(lock, mode=0o600)

    started = time.monotonic()
    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        timeout=5,
    )

    assert time.monotonic() - started < 5
    assert result.returncode in {3, 10}
    assert json.loads(result.stdout)['ok'] is False
    assert list(repo.glob('.venv-build.*')) == []


def test_install_rejects_dependency_lock_larger_than_four_mib(tmp_path):
    repo = make_test_repo(tmp_path)
    lock = repo / 'requirements.txt'
    lock.write_bytes(b'#' * (4 * 1024 * 1024 + 1))
    lock.chmod(0o600)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
        timeout=10,
    )

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload['exitKind'] == 'dependency_error'
    assert 'could not be read atomically' in payload['error']
    assert list(repo.glob('.venv-build.*')) == []


def test_install_checks_transaction_helper_permissions_before_execution(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = tmp_path / 'unsafe-helper-executed'
    helper = repo / 'scripts' / 'venv_transaction.py'
    helper.write_text(
        'from pathlib import Path\n'
        f'Path({str(marker)!r}).write_text("executed", encoding="utf-8")\n'
        'print("absent")\n',
        encoding='utf-8',
    )
    helper.chmod(0o666)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--json'],
        cwd=repo,
    )

    assert result.returncode == 10
    assert json.loads(result.stdout)['exitKind'] == 'install_error'
    assert not marker.exists()
    assert list(repo.glob('.venv-build.*')) == []


def test_install_refuses_concurrent_transaction_without_touching_venv(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    marker = repo / '.venv' / 'existing-marker'
    marker.write_text('preserve me', encoding='utf-8')
    lock = repo / '.venv.install.lock'
    lock.touch(mode=0o600)
    lock.chmod(0o600)
    holder = subprocess.Popen(
        [
            sys.executable,
            '-c',
            (
                'import fcntl, os, sys\n'
                'fd = os.open(sys.argv[1], os.O_RDWR)\n'
                'fcntl.flock(fd, fcntl.LOCK_EX)\n'
                'print("locked", flush=True)\n'
                'sys.stdin.read(1)\n'
            ),
            str(lock),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == 'locked'
        result = run_command(
            ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies'],
            cwd=repo,
        )
        assert result.returncode == 3
        assert 'already in progress' in result.stderr
        assert marker.read_text(encoding='utf-8') == 'preserve me'
        assert list(repo.glob('.venv-build.*')) == []
    finally:
        holder.stdin.write('\n')
        holder.stdin.flush()
        holder.wait(timeout=5)


def test_check_default_runs_only_offline_parsing_group(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    record = tmp_path / 'selfcheck-args.json'
    pytest_record = tmp_path / 'pytest-env.json'
    result_path_record = tmp_path / 'result-path.txt'
    marker = tmp_path / 'network-called'
    pytest_main_marker = tmp_path / 'pytest-main-called'
    hostile_bin = tmp_path / 'hostile-bin'
    hostile_bin.mkdir()
    hostile_tool_marker = tmp_path / 'hostile-tool-called'
    for name in ('python3', 'pytest', 'mktemp', 'shellcheck'):
        tool = hostile_bin / name
        tool.write_text(f'#!/bin/sh\ntouch {hostile_tool_marker}\nexit 99\n', encoding='utf-8')
        tool.chmod(0o755)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--quiet'],
        cwd=repo,
        env={
            'SELFCHECK_RECORD': str(record),
            'NETWORK_MARKER': str(marker),
            'CHECK_RESULT_PATH_RECORD': str(result_path_record),
            'PATH': str(hostile_bin),
            'TMPDIR': str(tmp_path / 'hostile-tmpdir'),
            'TMP': str(tmp_path / 'hostile-tmp'),
            'TEMP': str(tmp_path / 'hostile-temp'),
            'PYTEST_ADDOPTS': '--this-option-must-not-survive',
            'PYTEST_PLUGINS': 'untrusted_plugin',
            'PYTEST_DEBUG_TEMPROOT': str(tmp_path / 'hostile-pytest-temp-root'),
            'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '0',
            'CHECK_PYTEST_ENV_RECORD': str(pytest_record),
            'CHECK_PYTEST_MAIN_MARKER': str(pytest_main_marker),
            'SERPER_API_KEY': 'must-not-reach-offline-pytest',
            'SERPER_API_KEYS': 'must-not-reach-offline-pytest-either',
        },
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(record.read_text(encoding='utf-8')) == ['--group', 'parsing', '--compact']
    pytest_environment = json.loads(pytest_record.read_text(encoding='utf-8'))
    pytest_args = pytest_environment.pop('args')
    assert pytest_environment == {
        'addopts': None,
        'plugins': None,
        'debug_temproot': None,
        'autoload': '1',
        'SERPER_API_KEY': None,
        'SERPER_API_KEYS': None,
        'release_archive': None,
        'release_commit': None,
        'path': '/usr/local/bin:/usr/bin:/bin',
        'tmpdir': '/tmp',
        'tmp': '/tmp',
        'temp': '/tmp',
    }
    assert pytest_args[0] == str((repo / 'tests').resolve())
    assert '--noconftest' in pytest_args
    assert '--import-mode=importlib' in pytest_args
    result_path = Path(result_path_record.read_text(encoding='utf-8'))
    assert result_path.parent == Path('/tmp')
    assert not result_path.exists()
    assert not marker.exists()
    assert not pytest_main_marker.exists()
    assert not hostile_tool_marker.exists()
    assert list(tmp_path.glob('google-search-check-*')) == []


def test_check_release_arguments_are_paired_and_reach_only_the_pytest_protocol(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    archive = tmp_path / 'candidate.tar'
    archive.write_bytes(b'test candidate')
    archive.chmod(0o600)
    commit = 'a' * 40
    pytest_record = tmp_path / 'release-pytest-env.json'

    result = run_command(
        [
            '/bin/bash', '-p', 'scripts/check.sh', '--venv', '--quiet',
            '--release-archive', str(archive), '--release-commit', commit,
        ],
        cwd=repo,
        env={
            'CHECK_PYTEST_ENV_RECORD': str(pytest_record),
            'GOOGLE_SEARCH_RELEASE_ARCHIVE': '/tmp/ambient-must-not-win',
            'GOOGLE_SEARCH_RELEASE_COMMIT': 'b' * 40,
        },
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )

    assert result.returncode == 0, result.stderr
    pytest_environment = json.loads(pytest_record.read_text(encoding='utf-8'))
    assert pytest_environment['release_archive'] == str(archive)
    assert pytest_environment['release_commit'] == commit


@pytest.mark.parametrize(
    'arguments',
    (
        ('--release-archive', '/tmp/candidate.tar'),
        ('--release-commit', 'a' * 40),
        ('--release-archive', '', '--release-commit', ''),
        ('--release-archive', '/tmp/candidate.tar', '--release-commit', ''),
        ('--release-archive', '', '--release-commit', 'a' * 40),
        ('--release-archive', 'relative.tar', '--release-commit', 'a' * 40),
        ('--release-archive', '/tmp/candidate.tar', '--release-commit', 'not-an-oid'),
    ),
)
def test_check_rejects_incomplete_or_invalid_release_arguments(tmp_path, arguments):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--quiet', *arguments],
        cwd=repo,
    )

    assert result.returncode == 1


def test_check_source_snapshot_disables_repository_fsmonitor(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    git = shutil.which('git', path='/usr/bin:/bin')
    assert git is not None
    subprocess.run([git, 'init', '-q', str(repo)], check=True)
    (repo / '.git' / 'info' / 'exclude').write_text('.venv/\n', encoding='utf-8')
    subprocess.run([git, '-C', str(repo), 'add', '.'], check=True)
    marker = tmp_path / 'fsmonitor-ran'
    hook = tmp_path / 'fsmonitor-hook'
    hook.write_text(f'#!/bin/sh\ntouch {marker}\n', encoding='utf-8')
    hook.chmod(0o755)
    subprocess.run([git, '-C', str(repo), 'config', 'core.fsmonitor', str(hook)], check=True)

    subprocess.run([git, '-C', str(repo), 'status', '--porcelain'], check=True, capture_output=True)
    assert marker.exists(), 'control Git invocation did not exercise the configured fsmonitor'
    marker.unlink()

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--venv', '--quiet'],
        cwd=repo,
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_check_rejects_wrong_transitive_development_distribution_version(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo, with_pytest=True)
    site_packages = Path(
        subprocess.run(
            [str(python), '-I', '-c', 'import site; print(site.getsitepackages()[0])'],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    metadata = site_packages / 'pluggy-1.6.0.dist-info' / 'METADATA'
    metadata.write_text(
        metadata.read_text(encoding='utf-8').replace('Version: 1.6.0', 'Version: 0.0.0'),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--quiet'],
        cwd=repo,
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )

    assert result.returncode == 1
    assert 'pytest failed' in result.stderr


@pytest.mark.parametrize('mutation', ('add', 'replace'))
def test_check_detects_root_release_candidate_mutation_during_pytest(tmp_path, mutation):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    target = repo / ('release-candidate.txt' if mutation == 'add' else 'README.md')

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--quiet'],
        cwd=repo,
        env={
            'CHECK_PYTEST_SOURCE_MUTATION': mutation,
            'CHECK_PYTEST_SOURCE_MUTATION_TARGET': str(target),
        },
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )

    assert result.returncode == 1
    assert 'source tree or its parent chain changed during the check' in result.stderr


def test_check_rejects_pytest_main_that_returns_success_without_lifecycle_hooks(tmp_path):
    repo = make_test_repo(tmp_path)
    python = create_stub_runtime(repo, with_pytest=True)
    site_packages = Path(
        subprocess.run(
            [str(python), '-I', '-c', 'import site; print(site.getsitepackages()[0])'],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    (site_packages / '_pytest' / 'config' / '__init__.py').write_text(
        'class Config:\n    pass\n'
        'def main(args=None, plugins=None):\n'
        '    from pytest import ExitCode\n'
        '    return ExitCode.OK\n',
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--quiet'],
        cwd=repo,
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )
    assert result.returncode == 1
    assert 'pytest failed' in result.stderr


def test_check_online_preflight_preserves_config_exit_two_and_never_runs_network(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    marker = tmp_path / 'network-called'

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--online-smoke', '--quiet'],
        cwd=repo,
        env={'SERPER_API_KEY': '', 'NETWORK_MARKER': str(marker)},
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )
    assert result.returncode == 2
    assert 'valid Serper API key configuration' in result.stderr
    assert not marker.exists()


def test_check_online_preflight_keeps_unexpected_client_errors_at_exit_one(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    (repo / 'scripts' / 'client.py').write_text(
        'class SerperConfigError(Exception):\n    pass\n'
        'def load_api_keys():\n    raise RuntimeError("unexpected helper bug")\n',
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--online-smoke', '--quiet'],
        cwd=repo,
        env={'SERPER_API_KEY': 'test-only-key-123'},
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )
    assert result.returncode == 1
    assert 'preflight failed' in result.stderr


def test_check_rejects_empty_object_and_wrong_schema_for_every_result_mode(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)

    for target, flag in (
        ('parsing', None),
        ('smoke', '--online-smoke'),
        ('full', '--online-full'),
    ):
        for bad_mode in ('empty', 'object', 'schema'):
            path_record = tmp_path / f'{target}-{bad_mode}-path.txt'
            arguments = ['/bin/bash', '-p', 'scripts/check.sh', '--quiet']
            if flag:
                arguments.append(flag)
            result = run_command(
                arguments,
                cwd=repo,
                env={
                    'SERPER_API_KEY': 'test-only-key-123',
                    'CHECK_BAD_TARGET': target,
                    'CHECK_BAD_MODE': bad_mode,
                    'CHECK_RESULT_PATH_RECORD': str(path_record),
                },
                timeout=CHECK_INTEGRATION_TIMEOUT,
            )
            assert result.returncode == 1, (target, bad_mode, result.stderr)
            assert 'invalid result document' in result.stderr
            result_path = Path(path_record.read_text(encoding='utf-8'))
            assert result_path.parent == Path('/tmp')
            assert not result_path.exists()


def test_check_requires_exact_result_protocol_sentinel(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    protocol = repo / 'scripts' / 'check_protocol.py'
    protocol.write_text(
        protocol.read_text(encoding='utf-8').replace(
            "'google-search-parsing-result-ok-v1'",
            "'google-search-parsing-result-ok-v1-extra'",
            1,
        ),
        encoding='utf-8',
    )

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--quiet'],
        cwd=repo,
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )
    assert result.returncode == 1
    assert 'invalid sentinel' in result.stderr


def test_check_rejects_untrusted_test_tree_before_pytest_import(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    pytest_record = tmp_path / 'pytest-called'
    test_file = repo / 'tests' / 'test_placeholder.py'
    test_file.chmod(0o666)

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--quiet'],
        cwd=repo,
        env={'CHECK_PYTEST_ENV_RECORD': str(pytest_record)},
    )
    assert result.returncode == 1
    assert 'source tree or its parent chain is unsafe' in result.stderr
    assert not pytest_record.exists()


@pytest.mark.parametrize('unsafe_mode', (0o2777, 0o3777))
def test_check_requires_safe_tmp_to_have_exact_mode_01777(tmp_path, unsafe_mode):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    safe_tmp = tmp_path / 'candidate-tmp'
    safe_tmp.mkdir()
    safe_tmp.chmod(unsafe_mode)
    assert stat.S_IMODE(safe_tmp.stat().st_mode) == unsafe_mode

    check_script = repo / 'scripts' / 'check.sh'
    source = check_script.read_text(encoding='utf-8')
    source = source.replace('SAFE_TMP_DIR="/tmp"', f'SAFE_TMP_DIR={str(safe_tmp)!r}', 1)
    source = source.replace('[ "$owner" = "0" ] || return 1', '[ "$owner" = "$(id -u)" ] || return 1', 1)
    check_script.write_text(source, encoding='utf-8')

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--quiet'],
        cwd=repo,
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )

    assert result.returncode == 1
    assert 'failed to allocate parsing result file' in result.stderr
    assert list(safe_tmp.glob('google-search-check-*')) == []


def test_installer_never_modifies_system_packages_or_runs_pip_entrypoint():
    script = (ROOT / 'scripts' / 'install.sh').read_text(encoding='utf-8')
    locked_installer = (ROOT / 'scripts' / 'locked_install.py').read_text(encoding='utf-8')
    transaction = (ROOT / 'scripts' / 'venv_transaction.py').read_text(encoding='utf-8')
    assert 'apt-get' not in script
    assert 'pip install --upgrade' not in script
    assert '/bin/pip' not in script
    assert '-m pip --isolated --python "$candidate_python"' in script
    assert "'install'," in locked_installer
    assert "'--disable-pip-version-check'," in locked_installer
    assert "'--no-input'," in locked_installer
    assert 'PIP_CONFIG_FILE=/dev/null' in script
    assert "'--require-hashes'," in locked_installer
    assert "'--only-binary=:all:'," in locked_installer
    assert 'install_transaction "$DEV_REQ_FILE" development' in script
    assert '--expect-runtime-token "$SELECTED_RUNTIME_TOKEN"' in script
    assert 'run_selected_task check-keys' in script
    assert 'run_online_task smoke "$SMOKE_RESULT_PATH"' in script
    assert 'run_online_task full "$SELFCHECK_RESULT_PATH"' in script
    assert 'run_selected_task smoke' not in script
    assert 'run_selected_task full' not in script
    assert 'run_isolated_script' not in script
    assert 'SELECTED_PY' not in script
    assert '"$VENV_TRANSACTION_SCRIPT" publish' in script
    assert '"$LOCKED_INSTALL_SCRIPT"' in script
    assert '-r "$lock_file"' not in script
    assert 'renameat2' in transaction
    assert 'RENAME_NOREPLACE' in transaction
    assert 'RENAME_EXCHANGE' in transaction
    assert 'mv -- "$TEMP_VENV_DIR" "$VENV_DIR"' not in script
    assert '${TMPDIR' not in script
    assert '[ "$mode" = "1777" ]' in script


def test_install_term_waits_for_locked_helper_before_cleanup_and_lock_release(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo)
    write_local_runtime_lock(repo)
    started = tmp_path / 'locked-helper-started'
    observed = tmp_path / 'locked-helper-observed'
    helper = repo / 'scripts' / 'locked_install.py'
    helper.write_text(
        'import argparse, fcntl, os, signal, time\n'
        'from pathlib import Path\n'
        'parser = argparse.ArgumentParser()\n'
        'parser.add_argument("--base-dir", required=True)\n'
        'parser.add_argument("--lock-file")\n'
        'parser.add_argument("--expected-uid")\n'
        'parser.add_argument("--expected-snapshot")\n'
        'parser.add_argument("--candidate-python")\n'
        'parser.add_argument("--sentinel")\n'
        'args = parser.parse_args()\n'
        f'started = Path({str(started)!r})\n'
        f'observed = Path({str(observed)!r})\n'
        'base = Path(args.base_dir)\n'
        'def stop(signum, _frame):\n'
        '    lock_file = os.open(base / ".venv.install.lock", os.O_RDWR)\n'
        '    try:\n'
        '        try:\n'
        '            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)\n'
        '            lock_held = False\n'
        '        except BlockingIOError:\n'
        '            lock_held = True\n'
        '    finally:\n'
        '        os.close(lock_file)\n'
        '    builds = list(base.glob(".venv-build.*"))\n'
        '    bootstraps = list(base.glob(".venv-bootstrap.*"))\n'
        '    observed.write_text(f"{lock_held}|{bool(builds)}|{bool(bootstraps)}")\n'
        '    time.sleep(0.2)\n'
        '    raise SystemExit(128 + signum)\n'
        'for managed in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):\n'
        '    signal.signal(managed, stop)\n'
        'started.write_text(str(os.getpid()))\n'
        'while True:\n'
        '    time.sleep(1)\n',
        encoding='utf-8',
    )

    process = subprocess.Popen(
        ['/bin/bash', '-p', 'scripts/install.sh', '--install-dependencies', '--quiet'],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not started.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started.exists(), process.communicate(timeout=5)
        os.kill(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)

    assert process.returncode == 143, (stdout, stderr)
    assert observed.read_text(encoding='utf-8') == 'True|True|True'
    assert list(repo.glob('.venv-build.*')) == []
    assert list(repo.glob('.venv-bootstrap.*')) == []
    with (repo / '.venv.install.lock').open('a+b') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.skipif(shutil.which('git') is None, reason='Git is required')
def test_check_source_snapshot_does_not_read_ambient_git_config(tmp_path):
    repo = make_test_repo(tmp_path)
    create_stub_runtime(repo, with_pytest=True)
    fifo = tmp_path / 'blocking-git-include'
    os.mkfifo(fifo, 0o600)
    subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    with (repo / '.git' / 'config').open('a', encoding='utf-8') as config:
        config.write(f'\n[include]\n\tpath = {fifo}\n')

    result = run_command(
        ['/bin/bash', '-p', 'scripts/check.sh', '--quiet'],
        cwd=repo,
        timeout=CHECK_INTEGRATION_TIMEOUT,
    )

    assert result.returncode == 0, result.stderr


def test_lock_files_pin_every_package_with_sha256_hashes():
    for name in ('requirements.txt', 'requirements-dev.txt'):
        path = ROOT / name
        text = path.read_text(encoding='utf-8')
        starts = list(re.finditer(r'(?m)^[A-Za-z0-9][A-Za-z0-9_.-]*==[^\n]+', text))
        assert starts, name
        for index, match in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
            assert '--hash=sha256:' in text[match.start():end], match.group(0)
        assert stat.S_IMODE(path.stat().st_mode) == 0o644

    assert 'requests==2.34.2' in (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    assert 'pytest==9.1.1' in (ROOT / 'requirements-dev.txt').read_text(encoding='utf-8')
