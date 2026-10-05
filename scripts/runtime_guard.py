"""Validate executable trees before launching the skill's virtualenv Python."""

import argparse
import configparser
import os
from pathlib import Path
import stat
import sys


class RuntimeSecurityError(ValueError):
    pass


TRUSTED_OWNERS = {0, os.geteuid()}
TEMP_ROOTS = {Path('/tmp'), Path('/var/tmp')}
MAX_CONFIG_BYTES = 16 * 1024


def _metadata(path, *, allow_link=False):
    metadata = path.lstat()
    if metadata.st_uid not in TRUSTED_OWNERS:
        raise RuntimeSecurityError('runtime path has an untrusted owner')
    if stat.S_ISLNK(metadata.st_mode):
        if not allow_link:
            raise RuntimeSecurityError('runtime tree contains an unexpected symlink')
        return metadata
    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
        raise RuntimeSecurityError('runtime tree contains a special file')
    if metadata.st_mode & 0o022:
        if not (
            path in TEMP_ROOTS
            and metadata.st_uid == 0
            and stat.S_ISDIR(metadata.st_mode)
            and stat.S_IMODE(metadata.st_mode) == 0o1777
        ):
            raise RuntimeSecurityError('runtime path is group- or world-writable')
    return metadata


def trusted_path(path, *, allow_links=False, _seen=None):
    if '..' in Path(path).parts:
        raise RuntimeSecurityError('runtime paths must not contain parent traversal')
    path = Path(os.path.abspath(path))
    seen = set() if _seen is None else _seen
    for component in reversed((path, *path.parents)):
        metadata = _metadata(component, allow_link=allow_links)
        if stat.S_ISLNK(metadata.st_mode):
            if component in seen or len(seen) >= 40:
                raise RuntimeSecurityError('runtime contains a symlink loop')
            seen.add(component)
            target = Path(os.readlink(component))
            if not target.is_absolute():
                target = component.parent / target
            trusted_path(target, allow_links=True, _seen=seen)
    return path.resolve(strict=True)


def trusted_tree(root):
    trusted_path(root)
    for directory, directories, files in os.walk(root, followlinks=False, onerror=_walk_error):
        for name in (*directories, *files):
            _metadata(Path(directory) / name)


def _walk_error(error):
    raise error


def validate_sources(skill):
    trusted_path(skill)
    trusted_tree(skill / 'scripts')
    for name in ('search.py', 'runtime_guard.py'):
        if not (skill / 'scripts' / name).is_file():
            raise RuntimeSecurityError('required source file is missing')


def validate_runtime(runtime):
    trusted_path(runtime)
    config_path = runtime / 'pyvenv.cfg'
    _metadata(config_path)
    if config_path.stat().st_size > MAX_CONFIG_BYTES:
        raise RuntimeSecurityError('virtualenv configuration is too large')
    config = configparser.ConfigParser(interpolation=None, delimiters=('=',))
    config.read_string('[venv]\n' + config_path.read_text(encoding='utf-8'))
    if config.sections() != ['venv'] or config.defaults():
        raise RuntimeSecurityError('virtualenv configuration contains unexpected sections')
    home = Path(config['venv']['home'])
    if not home.is_absolute():
        raise RuntimeSecurityError('virtualenv home must be absolute')
    trusted_path(home, allow_links=True)
    executable = runtime / 'bin' / 'python'
    target = trusted_path(executable, allow_links=True)
    if not target.is_file() or not os.access(target, os.X_OK):
        raise RuntimeSecurityError('virtualenv interpreter is missing or not executable')
    if not executable.is_symlink() or home.resolve(strict=True) != target.parent:
        raise RuntimeSecurityError('virtualenv must link to its configured base interpreter')
    # -I -S still honors an adjacent ._pth file on supported Python builds.
    for directory in {runtime, runtime / 'bin', target.parent, home}:
        if any(directory.glob('*._pth')):
            raise RuntimeSecurityError('Python ._pth overrides are not supported')
    for directory, directories, files in os.walk(runtime, followlinks=False, onerror=_walk_error):
        for name in (*directories, *files):
            path = Path(directory) / name
            metadata = _metadata(path, allow_link=True)
            if not stat.S_ISLNK(metadata.st_mode):
                continue
            if path.parent == runtime / 'bin' and name.startswith('python'):
                if trusted_path(path, allow_links=True) != target:
                    raise RuntimeSecurityError('virtualenv interpreter links disagree')
            elif path == runtime / 'lib64' and path.resolve(strict=True) == runtime / 'lib':
                trusted_path(runtime / 'lib')
            else:
                raise RuntimeSecurityError('runtime tree contains an unexpected symlink')
    if not list((runtime / 'lib').glob('python*/site-packages')):
        raise RuntimeSecurityError('virtualenv site-packages is missing')


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--skill', required=True, type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--site-packages', type=Path)
    parsed = parser.parse_args(argv)
    try:
        validate_sources(parsed.skill)
        if parsed.runtime is not None:
            validate_runtime(parsed.runtime)
        if parsed.site_packages is not None:
            trusted_tree(parsed.site_packages)
    except (RuntimeSecurityError, OSError, ValueError, KeyError, configparser.Error):
        print('google-search: source or runtime ownership, permissions, or layout is unsafe', file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
