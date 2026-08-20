import importlib
import importlib.machinery
import importlib.metadata
import importlib.util
import os
import re
import runpy
import site
import stat
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

EXPECTED_DISTRIBUTIONS = (
    ('certifi', '2026.7.22', 'certifi'),
    ('charset-normalizer', '3.5.1', 'charset_normalizer'),
    ('idna', '3.19', 'idna'),
    ('requests', '2.34.2', 'requests'),
    ('urllib3', '2.7.0', 'urllib3'),
)
DEVELOPMENT_DISTRIBUTIONS = (
    ('iniconfig', '2.3.0', 'iniconfig'),
    ('packaging', '26.3', 'packaging'),
    ('pluggy', '1.6.0', 'pluggy'),
    ('pygments', '2.21.0', 'pygments'),
    ('pytest', '9.1.1', 'pytest'),
)
PYTHON_310_DEVELOPMENT_DISTRIBUTIONS = (
    ('exceptiongroup', '1.3.1', 'exceptiongroup'),
    ('tomli', '2.4.1', 'tomli'),
    ('typing-extensions', '4.16.0', 'typing_extensions'),
)
MINIMUM_PYTHON = (3, 10)
MAXIMUM_PYTHON = (3, 14)
PROBE_SENTINEL_PATTERN = re.compile(r'^[a-z0-9][a-z0-9-]{0,127}$')


class RuntimeGuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeLayout:
    roots: tuple
    origins: tuple
    claimed_files: tuple
    identities: tuple


def _canonical_distribution_name(value):
    return re.sub(r'[-_.]+', '-', str(value)).lower()


def _identity(metadata):
    values = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        metadata.st_mode,
    )
    if stat.S_ISREG(metadata.st_mode):
        values += (
            metadata.st_nlink,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
    return values


def _remember(path, metadata, identities):
    key = os.fsencode(path)
    value = _identity(metadata)
    previous = identities.setdefault(key, value)
    if previous != value:
        raise RuntimeGuardError('runtime path changed during validation')


def _validate_entry(path, identities, *, expected_type, allow_symlink=False):
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeGuardError('runtime path is unavailable') from error
    if metadata.st_uid not in {0, os.geteuid()}:
        raise RuntimeGuardError('runtime path has an unsafe owner')
    if stat.S_ISLNK(metadata.st_mode):
        if not allow_symlink:
            raise RuntimeGuardError('runtime path is a symlink')
    elif metadata.st_mode & 0o022:
        raise RuntimeGuardError('runtime path is group- or world-writable')
    if expected_type == 'directory' and not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeGuardError('runtime path is not a directory')
    if (
        expected_type == 'file'
        and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1)
    ):
        raise RuntimeGuardError('runtime path is not a single-link regular file')
    _remember(path, metadata, identities)
    return metadata


def _validate_directory_chain(path, identities):
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeGuardError('runtime path is not absolute')
    components = []
    current = Path(path.anchor)
    components.append(current)
    for component in path.parts[1:]:
        current /= component
        components.append(current)
    metadata_entries = []
    for current in components:
        try:
            metadata = current.lstat()
        except OSError as error:
            raise RuntimeGuardError('runtime path is unavailable') from error
        if metadata.st_uid not in {0, os.geteuid()} or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeGuardError('runtime directory chain is unsafe')
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeGuardError('runtime directory chain contains a symlink')
        metadata_entries.append(metadata)
        _remember(current, metadata, identities)
    for index, metadata in enumerate(metadata_entries):
        if not metadata.st_mode & 0o022:
            continue
        child_is_trusted = (
            index + 1 < len(metadata_entries)
            and metadata_entries[index + 1].st_uid in {0, os.geteuid()}
        )
        if stat.S_IMODE(metadata.st_mode) != 0o1777 or not child_is_trusted:
            raise RuntimeGuardError('runtime directory chain is writable')
    return path.resolve(strict=True)


def _validate_tree(root, identities):
    root = root.resolve(strict=True)
    _validate_entry(root, identities, expected_type='directory')
    for directory, child_directories, files in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        child_directories.sort()
        files.sort()
        directory_path = Path(directory)
        _validate_entry(directory_path, identities, expected_type='directory')
        for name in child_directories:
            _validate_entry(
                directory_path / name,
                identities,
                expected_type='directory',
            )
        for name in files:
            _validate_entry(
                directory_path / name,
                identities,
                expected_type='file',
            )


def _is_within(path, roots):
    return any(path == root or root in path.parents for root in roots)


def _validate_path_under_roots(path, roots, identities, *, expected_type):
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeGuardError('distribution path is not absolute')
    for root in roots:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(component in {'', '.', '..'} for component in relative.parts):
            raise RuntimeGuardError('distribution path contains an unsafe component')
        current = root
        for index, component in enumerate(relative.parts):
            current /= component
            entry_type = expected_type if index == len(relative.parts) - 1 else 'directory'
            _validate_entry(current, identities, expected_type=entry_type)
        return current.resolve(strict=True)
    raise RuntimeGuardError('distribution path is outside system site paths')


def _candidate_site_roots():
    candidates = list(site.getsitepackages())
    paths = sysconfig.get_paths()
    for key in ('purelib', 'platlib'):
        candidate = paths.get(key)
        if candidate:
            candidates.append(candidate)
    roots = []
    seen = set()
    for candidate in candidates:
        path = Path(candidate)
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        encoded = os.fsencode(resolved)
        if encoded not in seen:
            roots.append(resolved)
            seen.add(encoded)
    return tuple(roots)


def _is_startup_hook(path):
    name = path.name
    return (
        name.endswith('.pth')
        or name == 'sitecustomize'
        or name == 'usercustomize'
        or name.startswith(('sitecustomize.', 'usercustomize.'))
    )


def _validate_startup_hooks(root, identities):
    try:
        entries = tuple(root.iterdir())
    except OSError as error:
        raise RuntimeGuardError('system site directory cannot be listed') from error
    for path in entries:
        if not _is_startup_hook(path):
            continue
        try:
            metadata = path.lstat()
        except OSError as error:
            raise RuntimeGuardError('startup hook changed during validation') from error
        if stat.S_ISDIR(metadata.st_mode):
            _validate_tree(path, identities)
        else:
            _validate_entry(path, identities, expected_type='file')


def _distribution_candidates(distribution, roots):
    try:
        return tuple(
            importlib.metadata.Distribution.discover(
                name=distribution,
                path=[os.fspath(root) for root in roots],
            )
        )
    except (Exception, SystemExit) as error:
        raise RuntimeGuardError('distribution metadata discovery failed') from error


def _validate_distribution(distribution, version, module_name, roots, identities):
    candidates = _distribution_candidates(distribution, roots)
    if len(candidates) != 1:
        raise RuntimeGuardError('a locked distribution is missing or ambiguous')
    candidate = candidates[0]
    metadata_path = _validate_path_under_roots(
        Path(candidate._path),
        roots,
        identities,
        expected_type='directory',
    )
    _validate_tree(metadata_path, identities)
    try:
        actual_name = _canonical_distribution_name(candidate.metadata['Name'])
        actual_version = candidate.version
        files = candidate.files
    except (Exception, SystemExit) as error:
        raise RuntimeGuardError('distribution metadata is invalid') from error
    if actual_name != _canonical_distribution_name(distribution) or actual_version != version:
        raise RuntimeGuardError('a locked distribution has the wrong identity or version')
    if not files:
        raise RuntimeGuardError('a locked distribution has no installed-file manifest')

    claimed_files = set()
    for relative_path in files:
        try:
            raw_installed_path = Path(candidate.locate_file(relative_path))
            installed_path = Path(os.path.abspath(os.fspath(raw_installed_path)))
        except (OSError, TypeError, ValueError) as error:
            raise RuntimeGuardError('a distribution file path is invalid') from error
        if not _is_within(installed_path, roots):
            continue
        try:
            metadata = installed_path.lstat()
        except OSError as error:
            raise RuntimeGuardError('a distribution file is missing') from error
        if stat.S_ISDIR(metadata.st_mode):
            installed_path = _validate_path_under_roots(
                installed_path,
                roots,
                identities,
                expected_type='directory',
            )
        else:
            installed_path = _validate_path_under_roots(
                installed_path,
                roots,
                identities,
                expected_type='file',
            )
            claimed_files.add(installed_path)

    importlib.invalidate_caches()
    try:
        specification = importlib.machinery.PathFinder.find_spec(
            module_name,
            [os.fspath(root) for root in roots],
        )
    except (Exception, SystemExit) as error:
        raise RuntimeGuardError('locked module discovery failed') from error
    if specification is None or specification.origin in {None, 'built-in', 'frozen'}:
        raise RuntimeGuardError('a locked module has no concrete origin')
    origin = _validate_path_under_roots(
        Path(specification.origin),
        roots,
        identities,
        expected_type='file',
    )
    if origin not in claimed_files or not _is_within(origin, roots):
        raise RuntimeGuardError('locked module origin is not owned by its distribution')
    locations = specification.submodule_search_locations
    if not locations:
        raise RuntimeGuardError('a locked module is not an installed package')
    for location in locations:
        package_root = _validate_path_under_roots(
            Path(location),
            roots,
            identities,
            expected_type='directory',
        )
        _validate_tree(package_root, identities)
    return (
        (module_name, os.fspath(origin)),
        tuple(sorted(os.fspath(path) for path in claimed_files)),
    )


def _profile_distributions(profile):
    if profile == 'runtime':
        return EXPECTED_DISTRIBUTIONS
    if profile == 'development':
        extras = DEVELOPMENT_DISTRIBUTIONS
        if sys.version_info[:2] < (3, 11):
            extras += PYTHON_310_DEVELOPMENT_DISTRIBUTIONS
        return EXPECTED_DISTRIBUTIONS + extras
    raise RuntimeGuardError('invalid runtime profile')


def inspect_runtime(site_roots=None, profile='runtime'):
    if not MINIMUM_PYTHON <= sys.version_info[:2] <= MAXIMUM_PYTHON:
        raise RuntimeGuardError('unsupported Python version')
    roots = tuple(Path(root).resolve(strict=True) for root in (
        _candidate_site_roots() if site_roots is None else site_roots
    ))
    if not roots:
        raise RuntimeGuardError('system Python has no site directories')
    identities = {}
    checked_roots = []
    for root in roots:
        checked_root = _validate_directory_chain(root, identities)
        if checked_root not in checked_roots:
            checked_roots.append(checked_root)
            _validate_startup_hooks(checked_root, identities)
    roots = tuple(checked_roots)
    validated_distributions = tuple(
        _validate_distribution(distribution, version, module_name, roots, identities)
        for distribution, version, module_name in _profile_distributions(profile)
    )
    origins = tuple(result[0] for result in validated_distributions)
    claimed_files = []
    for _origin, distribution_files in validated_distributions:
        claimed_files.extend(distribution_files)
    if len(claimed_files) != len(set(claimed_files)):
        raise RuntimeGuardError('locked distributions claim the same installed file')
    return RuntimeLayout(
        roots=tuple(os.fspath(root) for root in roots),
        origins=origins,
        claimed_files=tuple(sorted(claimed_files)),
        identities=tuple(sorted(identities.items())),
    )


class _LockedSiteFinder:
    def __init__(self, layout):
        self._roots = tuple(Path(root) for root in layout.roots)
        self._claimed_files = frozenset(Path(path) for path in layout.claimed_files)

    def _resolve(self, value):
        try:
            return Path(value).resolve(strict=True)
        except (OSError, TypeError, ValueError) as error:
            raise RuntimeGuardError('an imported module has an invalid origin') from error

    def find_spec(self, fullname, path=None, target=None):
        try:
            specification = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        except RuntimeGuardError:
            raise
        except (Exception, SystemExit) as error:
            raise RuntimeGuardError('module discovery failed') from error
        if specification is None:
            return None

        origin = specification.origin
        site_origin = None
        if origin not in {None, 'built-in', 'frozen'}:
            resolved_origin = self._resolve(origin)
            if _is_within(resolved_origin, self._roots):
                site_origin = resolved_origin
                if resolved_origin not in self._claimed_files:
                    raise RuntimeGuardError(
                        'an unlocked system-site module was blocked before import'
                    )

        site_locations = []
        for location in specification.submodule_search_locations or ():
            resolved_location = self._resolve(location)
            if _is_within(resolved_location, self._roots):
                site_locations.append(resolved_location)
        if site_locations and site_origin is None:
            raise RuntimeGuardError(
                'an unlocked system-site namespace was blocked before import'
            )
        return specification

    def find_distributions(self, context=None):
        try:
            return importlib.machinery.PathFinder.find_distributions(context)
        except (Exception, SystemExit) as error:
            raise RuntimeGuardError('distribution metadata discovery failed') from error


def _install_locked_site_finder(layout):
    indexes = [
        index
        for index, finder in enumerate(sys.meta_path)
        if finder is importlib.machinery.PathFinder
    ]
    if len(indexes) != 1:
        raise RuntimeGuardError('Python path importer state is unexpected')
    finder = _LockedSiteFinder(layout)
    sys.meta_path[indexes[0]] = finder
    return finder


def _verify_imports(layout):
    expected_origins = dict(layout.origins)
    for module_name in expected_origins:
        if module_name in sys.modules:
            raise RuntimeGuardError('a locked module was imported before validation')
    _install_locked_site_finder(layout)
    sys.path.extend(layout.roots)
    imported = {}
    for module_name, expected_origin in layout.origins:
        module = importlib.import_module(module_name)
        specification = getattr(module, '__spec__', None)
        origin = getattr(specification, 'origin', None)
        if origin is None or os.fspath(Path(origin).resolve(strict=True)) != expected_origin:
            raise RuntimeGuardError('a locked module was imported from an unexpected origin')
        imported[module_name] = module

    required_apis = (
        imported['certifi'].where,
        imported['charset_normalizer'].from_bytes,
        imported['idna'].encode,
        imported['idna'].decode,
        imported['requests'].Session,
        imported['urllib3'].PoolManager,
    )
    if not all(callable(api) for api in required_apis):
        raise RuntimeGuardError('a locked package is missing a required API')
    requests = imported['requests']
    for error_type in (requests.RequestException, requests.Timeout):
        if not isinstance(error_type, type) or not issubclass(error_type, Exception):
            raise RuntimeGuardError('requests exposes an invalid error API')
    session = requests.Session()
    try:
        if not callable(getattr(session, 'post', None)):
            raise RuntimeGuardError('requests session has no post method')
    finally:
        close = getattr(session, 'close', None)
        if callable(close):
            close()


def validate_and_activate(site_roots=None, profile='runtime'):
    before = inspect_runtime(site_roots, profile)
    if inspect_runtime(site_roots, profile) != before:
        raise RuntimeGuardError('system runtime changed during validation')
    _verify_imports(before)
    if inspect_runtime(site_roots, profile) != before:
        raise RuntimeGuardError('system runtime changed while packages were imported')
    return before


def _require_safe_startup():
    if not (
        sys.flags.isolated
        and sys.flags.no_site
        and sys.flags.no_user_site
        and sys.flags.ignore_environment
    ):
        raise RuntimeGuardError('runtime guard requires Python -I -S')


def _parse_bootstrap_layout(executable, minor, stdlib_list, zip_list):
    if not re.fullmatch(r'(?:10|11|12|13|14)', minor):
        raise RuntimeGuardError('invalid static Python minor version')
    try:
        resolved_executable = Path(executable).resolve(strict=True)
        stdlibs = {Path(value).resolve(strict=True) for value in stdlib_list.split(':')}
        zips = {Path(value).resolve(strict=False) for value in zip_list.split(':')}
    except (OSError, RuntimeError, ValueError):
        raise RuntimeGuardError('invalid static Python layout') from None
    if not stdlibs or not zips or '' in stdlib_list.split(':') or '' in zip_list.split(':'):
        raise RuntimeGuardError('incomplete static Python layout')
    return resolved_executable, int(minor), stdlibs, zips


def _origin_is_in_stdlib(raw_origin, stdlibs, zips):
    if not raw_origin:
        return False
    for zip_path in zips:
        if raw_origin.startswith(f'{zip_path}{os.sep}'):
            return True
    try:
        origin = Path(raw_origin).resolve(strict=True)
    except OSError:
        return False
    return any(origin == root or root in origin.parents for root in stdlibs)


def _validate_bootstrap_layout(executable, minor, stdlib_list, zip_list):
    resolved_executable, expected_minor, stdlibs, zips = _parse_bootstrap_layout(
        executable,
        minor,
        stdlib_list,
        zip_list,
    )
    if sys.version_info[:2] != (3, expected_minor):
        raise RuntimeGuardError('Python version does not match the static layout')
    actual_executable = Path(sys.executable).resolve(strict=True)
    base_executable = Path(getattr(sys, '_base_executable', sys.executable)).resolve(strict=True)
    if actual_executable != resolved_executable or base_executable != resolved_executable:
        raise RuntimeGuardError('Python executable does not match the static layout')
    if Path(sys.prefix).resolve(strict=True) != Path(sys.base_prefix).resolve(strict=True):
        raise RuntimeGuardError('system runtime unexpectedly initialized a virtual environment')
    allowed_paths = stdlibs | zips
    allowed_paths.update(root / 'lib-dynload' for root in stdlibs)
    for raw_path in sys.path:
        if not raw_path or Path(raw_path).resolve(strict=False) not in allowed_paths:
            raise RuntimeGuardError('Python path does not match the static layout')
    import encodings
    if not _origin_is_in_stdlib(getattr(os, '__file__', None), stdlibs, zips):
        raise RuntimeGuardError('os was imported outside the static stdlib layout')
    if not _origin_is_in_stdlib(getattr(encodings, '__file__', None), stdlibs, zips):
        raise RuntimeGuardError('encodings was imported outside the static stdlib layout')


def _run_script(script_name, arguments):
    raw_script = Path(script_name)
    try:
        before = raw_script.lstat()
        script = raw_script.resolve(strict=True)
        after = script.lstat()
    except OSError as error:
        raise RuntimeGuardError('target script is unavailable') from error
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid not in {0, os.geteuid()}
        or before.st_mode & 0o022
        or before.st_nlink != 1
        or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        or script.parent != Path(__file__).resolve(strict=True).parent
    ):
        raise RuntimeGuardError('target script is unsafe')
    sys.argv = [os.fspath(script), *arguments]
    sys.path.append(os.fspath(script.parent))
    runpy.run_path(os.fspath(script), run_name='__main__')


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        _require_safe_startup()
        if not arguments:
            raise RuntimeGuardError('missing runtime guard command')
        command = arguments.pop(0)
        if command == 'probe':
            if len(arguments) != 6 or not PROBE_SENTINEL_PATTERN.fullmatch(arguments[0]):
                raise RuntimeGuardError('invalid runtime probe sentinel')
            _validate_bootstrap_layout(*arguments[1:5])
            validate_and_activate(profile=arguments[5])
            print(arguments[0])
            return 0
        if command == 'run':
            if len(arguments) < 6:
                raise RuntimeGuardError('missing target script')
            _validate_bootstrap_layout(*arguments[:4])
            profile = arguments[4]
            script = arguments[5]
            arguments = arguments[6:]
            validate_and_activate(profile=profile)
            _run_script(script, arguments)
            return 0
        raise RuntimeGuardError('invalid runtime guard command')
    except RuntimeGuardError:
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
