import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import secure_io
from secure_io import OutputSecurityError


def test_secure_write_is_atomic_private_and_compatible_wrapper_works(tmp_path, monkeypatch):
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    target = tmp_path / 'nested' / 'result.json'

    assert secure_io.secure_write_text('first', target) == target
    assert target.read_text(encoding='utf-8') == 'first'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert secure_io.atomic_write_text(target, 'second', mode=0o600) == target
    assert target.read_text(encoding='utf-8') == 'second'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(target.parent.glob(f'.{target.name}.*.tmp'))


def test_secure_write_rejects_path_outside_allowed_roots():
    with pytest.raises(OutputSecurityError, match='output path must be under'):
        secure_io.secure_write_text('no', '/etc/google-search-result.json')


def test_output_target_normalization_rejects_an_explicit_empty_path():
    with pytest.raises(OutputSecurityError, match='must not be empty'):
        secure_io.normalize_output_target('')
    with pytest.raises(OutputSecurityError, match='must not be empty'):
        secure_io.normalize_output_target('   ')


@pytest.mark.parametrize('path', ['../runtime/result.json', '../../tmp/result.json'])
def test_relative_output_target_cannot_escape_the_skill_output_directory(path):
    with pytest.raises(OutputSecurityError, match='escapes the skill output directory'):
        secure_io.normalize_output_target(path)


def test_output_target_normalization_rejects_an_unresolvable_user_home():
    with pytest.raises(OutputSecurityError, match='unresolvable user home'):
        secure_io.normalize_output_target('~google-search-user-that-must-not-exist/result.json')


def test_configured_root_rejects_an_unresolvable_user_home(monkeypatch):
    monkeypatch.setenv(
        secure_io.OUTPUT_DIR_ENV,
        '~google-search-output-user-that-must-not-exist/results',
    )
    with pytest.raises(OutputSecurityError, match='unresolvable user home'):
        secure_io.preflight_output_path('/tmp/google-search-result.json')


def test_preflight_normalizes_root_inspection_errors(monkeypatch):
    monkeypatch.setattr(secure_io.os, 'lstat', lambda path: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(OutputSecurityError, match='could not be securely inspected'):
        secure_io.preflight_output_path('/tmp/google-search-result.json')


def test_preflight_allows_missing_nested_parents_without_creating_them(tmp_path):
    target = tmp_path / 'missing' / 'nested' / 'result.json'

    assert secure_io.preflight_output_path(target) == target
    assert not (tmp_path / 'missing').exists()

    assert secure_io.secure_write_text('payload', target) == target
    assert target.read_text(encoding='utf-8') == 'payload'


@pytest.mark.parametrize(
    'unsafe_character',
    ['\x00', '\x1f', '\x7f', '\x85', '\u061c', '\u200e', '\u2028', '\u2029', '\u202e', '\u2066', '\ud800'],
)
def test_secure_write_rejects_control_directional_and_surrogate_path_characters(
    tmp_path, unsafe_character,
):
    target = os.fspath(tmp_path) + f'/unsafe{unsafe_character}name.json'

    with pytest.raises(OutputSecurityError, match='unsafe control or directional characters'):
        secure_io.secure_write_text('no', target)

    assert list(tmp_path.iterdir()) == []


def test_tmpdir_environment_cannot_expand_allowed_roots(monkeypatch):
    monkeypatch.setenv('TMPDIR', '/etc')
    assert Path('/etc') not in secure_io._configured_roots()


def test_secure_write_rejects_target_symlink_and_preserves_victim(tmp_path, monkeypatch):
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    victim = tmp_path / 'victim'
    victim.write_text('unchanged', encoding='utf-8')
    target = tmp_path / 'result'
    target.symlink_to(victim)

    with pytest.raises(OutputSecurityError):
        secure_io.secure_write_text('attack', target)
    assert victim.read_text(encoding='utf-8') == 'unchanged'


def test_secure_write_rejects_lexical_parent_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    real_parent = tmp_path / 'real-parent'
    real_parent.mkdir(mode=0o700)
    linked_parent = tmp_path / 'linked-parent'
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(OutputSecurityError, match='symlink'):
        secure_io.secure_write_text('attack', linked_parent / 'result')
    assert not (real_parent / 'result').exists()


def test_configured_root_rejects_symlink_in_its_own_ancestry(tmp_path, monkeypatch):
    real_root = tmp_path / 'real-root'
    output = real_root / 'output'
    output.mkdir(parents=True, mode=0o700)
    linked_root = tmp_path / 'linked-root'
    linked_root.symlink_to(real_root, target_is_directory=True)
    configured_root = linked_root / 'output'
    monkeypatch.setattr(secure_io, '_configured_roots', lambda: [configured_root])

    with pytest.raises(OutputSecurityError, match='symlink'):
        secure_io.secure_write_text('attack', configured_root / 'result')
    assert not (output / 'result').exists()


def test_configured_root_rejects_a_foreign_owned_ancestor(tmp_path, monkeypatch):
    ancestor = tmp_path / 'foreign-parent'
    configured_root = ancestor / 'output'
    configured_root.mkdir(parents=True, mode=0o700)
    target = configured_root / 'result'
    real_lstat = secure_io.os.lstat

    def foreign_ancestor(path):
        metadata = real_lstat(path)
        if Path(path) == ancestor:
            return SimpleNamespace(st_mode=metadata.st_mode, st_uid=123456789)
        return metadata

    monkeypatch.setattr(secure_io, '_configured_roots', lambda: [configured_root])
    monkeypatch.setattr(secure_io.os, 'lstat', foreign_ancestor)

    with pytest.raises(OutputSecurityError, match='unsafe ancestor'):
        secure_io.secure_write_text('must not be written', target)
    assert not target.exists()


@pytest.mark.parametrize(('owner', 'mode'), [(123456789, 0o1777), (0, 0o0777), (0, 0o2777)])
def test_system_temporary_roots_require_root_owner_and_exact_mode_01777(owner, mode):
    metadata = SimpleNamespace(st_uid=owner, st_mode=stat.S_IFDIR | mode)

    with pytest.raises(OutputSecurityError, match='root-owned with exact mode 01777'):
        secure_io._validate_directory_metadata(Path('/tmp'), metadata)


def test_secure_write_rejects_existing_hardlinked_target(tmp_path, monkeypatch):
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    target = tmp_path / 'target'
    target.write_text('original', encoding='utf-8')
    target.chmod(0o600)
    alias = tmp_path / 'alias'
    os.link(target, alias)
    with pytest.raises(OutputSecurityError, match='hard links'):
        secure_io.secure_write_text('new', target)
    assert target.read_text(encoding='utf-8') == 'original'


def test_parent_inode_is_rechecked_before_install(tmp_path, monkeypatch):
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    target = tmp_path / 'result'
    real_stat = secure_io.os.stat
    parent_stat_calls = 0

    def changing_stat(path, *args, **kwargs):
        nonlocal parent_stat_calls
        result = real_stat(path, *args, **kwargs)
        if Path(path) == tmp_path and kwargs.get('follow_symlinks') is False:
            parent_stat_calls += 1
            if parent_stat_calls >= 2:
                return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino + 1)
        return result

    monkeypatch.setattr(secure_io.os, 'stat', changing_stat)
    with pytest.raises(OutputSecurityError, match='parent changed'):
        secure_io.secure_write_text('data', target)
    assert not target.exists()


def test_secure_write_does_not_chmod_a_path_after_atomic_replace(tmp_path, monkeypatch):
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    monkeypatch.setattr(secure_io.os, 'chmod', lambda *args, **kwargs: pytest.fail('path chmod is unsafe'))
    target = tmp_path / 'result'
    secure_io.secure_write_text('data', target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_secure_io_cli_reads_stdin_and_rejects_unknown_args(tmp_path):
    target = tmp_path / 'cli-output'
    environment = os.environ.copy()
    environment['TMPDIR'] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / 'secure_io.py'), '--path', str(target)],
        input='payload', text=True, capture_output=True, env=environment, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding='utf-8') == 'payload'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    unknown = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / 'secure_io.py'), '--unknown'],
        input='', text=True, capture_output=True, env=environment, check=False,
    )
    assert unknown.returncode == 2


def test_output_size_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    monkeypatch.setattr(secure_io, 'MAX_OUTPUT_BYTES', 4)
    with pytest.raises(OutputSecurityError, match='exceeds'):
        secure_io.secure_write_text('12345', tmp_path / 'too-large')


def test_maximum_length_target_name_does_not_expand_the_temporary_name(tmp_path):
    name_max = os.pathconf(tmp_path, 'PC_NAME_MAX')
    target = tmp_path / ('x' * name_max)

    assert secure_io.secure_write_text('payload', target) == target
    assert target.read_text(encoding='utf-8') == 'payload'
    assert not list(tmp_path.glob('.google-search-*.tmp'))
