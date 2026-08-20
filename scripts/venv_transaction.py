import ctypes
import fcntl
import os
import stat
import sys
from pathlib import Path


PUBLISH_SENTINEL = 'google-search-venv-published-v1'
ROLLBACK_SENTINEL = 'google-search-venv-rolled-back-v1'
STAGED_SENTINEL = 'google-search-staged-venv-verified-v1'
STAGED_PRESERVE_SENTINEL = 'google-search-staged-venv-preserve-v1'
RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2


def _identity(metadata):
    return f'{metadata.st_dev}:{metadata.st_ino}'


def _parse_identity(value):
    if value == 'absent':
        return None
    try:
        device, inode = value.split(':', 1)
        return int(device), int(inode)
    except (TypeError, ValueError):
        raise RuntimeError('invalid inode identity') from None


def _same_identity(metadata, expected):
    return expected is not None and (metadata.st_dev, metadata.st_ino) == expected


def _validate_lock(descriptor, path, expected_uid):
    opened = os.fstat(descriptor)
    named = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode):
        raise RuntimeError('transaction lock is not a regular file')
    if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        raise RuntimeError('transaction lock pathname changed')
    if (
        opened.st_uid != expected_uid
        or opened.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) != 0o600
    ):
        raise RuntimeError('transaction lock metadata changed')

    flags = (
        os.O_RDWR
        | getattr(os, 'O_CLOEXEC', 0)
        | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_NONBLOCK', 0)
    )
    independent_descriptor = os.open(path, flags)
    try:
        independent = os.fstat(independent_descriptor)
        if (
            not stat.S_ISREG(independent.st_mode)
            or (independent.st_dev, independent.st_ino) != (opened.st_dev, opened.st_ino)
            or independent.st_uid != expected_uid
            or independent.st_nlink != 1
            or stat.S_IMODE(independent.st_mode) != 0o600
        ):
            raise RuntimeError('transaction lock changed during retention probe')
        try:
            fcntl.flock(independent_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(independent_descriptor, fcntl.LOCK_UN)
            raise RuntimeError('transaction lock is no longer held by the installer')
    finally:
        os.close(independent_descriptor)


def _decode_mount_path(value):
    for encoded, decoded in (('\\040', ' '), ('\\011', '\t'), ('\\012', '\n'), ('\\134', '\\')):
        value = value.replace(encoded, decoded)
    return Path(value)


def _contains_submount(root):
    root = root.resolve(strict=True)
    try:
        lines = Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines()
    except OSError as error:
        raise RuntimeError('cannot inspect Linux mount table') from error
    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            raise RuntimeError('invalid Linux mount table entry')
        mount_path = Path(os.path.realpath(_decode_mount_path(fields[4])))
        try:
            mount_path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _raise_walk_error(error):
    raise RuntimeError('cannot completely inspect existing .venv') from error


def _inspect_removable_directory(target, expected_uid, expected_identity=None):
    metadata = target.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or target.is_symlink():
        raise RuntimeError('venv is not a real directory')
    if expected_identity is not None and not _same_identity(metadata, expected_identity):
        raise RuntimeError('venv identity changed before inspection')
    if metadata.st_uid != expected_uid or metadata.st_mode & 0o022:
        raise RuntimeError('venv has unsafe ownership or permissions')
    if _contains_submount(target):
        raise RuntimeError('venv contains or is a mount point')

    visited_root = False
    try:
        for directory, child_directories, _ in os.walk(
            target,
            topdown=True,
            onerror=_raise_walk_error,
            followlinks=False,
        ):
            directory_metadata = os.lstat(directory)
            if not visited_root:
                if (directory_metadata.st_dev, directory_metadata.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    raise RuntimeError('venv changed during inspection')
                visited_root = True
            if (
                not stat.S_ISDIR(directory_metadata.st_mode)
                or directory_metadata.st_dev != metadata.st_dev
                or directory_metadata.st_uid != expected_uid
                or stat.S_IMODE(directory_metadata.st_mode) & 0o300 != 0o300
            ):
                raise RuntimeError('venv contains a directory that cannot be safely removed')

            safe_children = []
            for name in child_directories:
                child_metadata = os.lstat(Path(directory) / name)
                if stat.S_ISLNK(child_metadata.st_mode):
                    continue
                if not stat.S_ISDIR(child_metadata.st_mode) or child_metadata.st_dev != metadata.st_dev:
                    raise RuntimeError('venv contains an unsafe directory entry')
                safe_children.append(name)
            child_directories[:] = safe_children
    except OSError as error:
        raise RuntimeError('cannot completely inspect venv') from error
    if not visited_root:
        raise RuntimeError('venv traversal returned no entries')
    final_metadata = target.lstat()
    if (final_metadata.st_dev, final_metadata.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise RuntimeError('venv changed during inspection')
    if _contains_submount(target):
        raise RuntimeError('venv gained a mount point during inspection')
    return metadata


def _inspect_existing(base, expected_uid):
    target = base / '.venv'
    try:
        metadata = _inspect_removable_directory(target, expected_uid)
    except FileNotFoundError:
        return 'absent'
    return _identity(metadata)


def _renameat2(directory_fd, source, destination, flags):
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, 'renameat2', None)
    if renameat2 is None:
        raise RuntimeError('renameat2 is unavailable')
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        directory_fd,
        os.fsencode(source),
        directory_fd,
        os.fsencode(destination),
        flags,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _open_base(raw_base):
    base = Path(raw_base).resolve(strict=True)
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    return base, os.open(base, flags)


def _validate_candidate_name(name):
    if '/' in name or not name.startswith('.venv-build.') or name in {'.', '..'}:
        raise RuntimeError('invalid candidate venv name')


def _publish(base, candidate_name, original_identity, candidate_identity, lock):
    _validate_candidate_name(candidate_name)
    _, directory_fd = _open_base(base)
    try:
        _validate_lock(*lock)
        candidate = os.stat(candidate_name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISDIR(candidate.st_mode) or not _same_identity(candidate, candidate_identity):
            raise RuntimeError('candidate venv changed before publication')
        if _contains_submount(base / candidate_name):
            raise RuntimeError('candidate venv contains or is a mount point')
        try:
            destination = os.stat('.venv', dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            destination = None

        if original_identity is None:
            if destination is not None:
                raise RuntimeError('destination appeared before publication')
            rename_flags = RENAME_NOREPLACE
        else:
            if destination is None or not stat.S_ISDIR(destination.st_mode):
                raise RuntimeError('destination disappeared before publication')
            if not _same_identity(destination, original_identity):
                raise RuntimeError('destination changed before publication')
            inspected_identity = _parse_identity(_inspect_existing(base, lock[2]))
            if inspected_identity != original_identity:
                raise RuntimeError('destination changed during final inspection')
            rename_flags = RENAME_EXCHANGE

        _validate_lock(*lock)
        candidate = os.stat(candidate_name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISDIR(candidate.st_mode) or not _same_identity(candidate, candidate_identity):
            raise RuntimeError('candidate venv changed immediately before publication')
        _renameat2(directory_fd, candidate_name, '.venv', rename_flags)
        installed = os.stat('.venv', dir_fd=directory_fd, follow_symlinks=False)
        if not _same_identity(installed, candidate_identity):
            raise RuntimeError('published venv identity mismatch')
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return PUBLISH_SENTINEL


def _rollback(base, candidate_name, original_identity, candidate_identity, lock):
    _validate_candidate_name(candidate_name)
    _, directory_fd = _open_base(base)
    try:
        _validate_lock(*lock)
        installed = os.stat('.venv', dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISDIR(installed.st_mode) or not _same_identity(installed, candidate_identity):
            raise RuntimeError('live venv is not the published candidate')

        try:
            staged = os.stat(candidate_name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            staged = None
        if original_identity is None:
            if staged is not None:
                raise RuntimeError('candidate rollback path is occupied')
            _renameat2(directory_fd, '.venv', candidate_name, RENAME_NOREPLACE)
            try:
                os.stat('.venv', dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise RuntimeError('new destination remained after rollback')
        else:
            if staged is None or not stat.S_ISDIR(staged.st_mode):
                raise RuntimeError('original venv is unavailable for rollback')
            if not _same_identity(staged, original_identity):
                raise RuntimeError('original venv changed before rollback')
            _renameat2(directory_fd, '.venv', candidate_name, RENAME_EXCHANGE)
            restored = os.stat('.venv', dir_fd=directory_fd, follow_symlinks=False)
            if not _same_identity(restored, original_identity):
                raise RuntimeError('original venv identity mismatch after rollback')
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return ROLLBACK_SENTINEL


def _verify_staged(base, candidate_name, original_identity, candidate_identity, lock):
    _validate_candidate_name(candidate_name)
    if original_identity is None:
        raise RuntimeError('no original venv exists for staged verification')
    _, directory_fd = _open_base(base)
    try:
        _validate_lock(*lock)
        installed = os.stat('.venv', dir_fd=directory_fd, follow_symlinks=False)
        staged = os.stat(candidate_name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISDIR(installed.st_mode) or not _same_identity(installed, candidate_identity):
            raise RuntimeError('live venv changed before staged cleanup')
        if not stat.S_ISDIR(staged.st_mode) or not _same_identity(staged, original_identity):
            raise RuntimeError('staged original venv changed before cleanup')
        removable = True
        try:
            _inspect_removable_directory(base / candidate_name, lock[2], original_identity)
        except (OSError, RuntimeError):
            removable = False
        _validate_lock(*lock)
        installed = os.stat('.venv', dir_fd=directory_fd, follow_symlinks=False)
        staged = os.stat(candidate_name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISDIR(installed.st_mode) or not _same_identity(installed, candidate_identity):
            raise RuntimeError('live venv changed during staged verification')
        if not stat.S_ISDIR(staged.st_mode) or not _same_identity(staged, original_identity):
            raise RuntimeError('staged original venv changed during verification')
    finally:
        os.close(directory_fd)
    return STAGED_SENTINEL if removable else STAGED_PRESERVE_SENTINEL


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) < 5:
        raise SystemExit(2)
    action, raw_base, lock_fd, lock_path, raw_uid, *rest = arguments
    expected_uid = int(raw_uid)
    lock = (int(lock_fd), lock_path, expected_uid)
    base = Path(raw_base).resolve(strict=True)

    if action == 'inspect' and not rest:
        _validate_lock(*lock)
        print(_inspect_existing(base, expected_uid))
        return 0
    if action in {'publish', 'rollback', 'verify-staged'} and len(rest) == 3:
        candidate_name, raw_original, raw_candidate = rest
        original_identity = _parse_identity(raw_original)
        candidate_identity = _parse_identity(raw_candidate)
        if candidate_identity is None:
            raise RuntimeError('candidate identity must be present')
        functions = {
            'publish': _publish,
            'rollback': _rollback,
            'verify-staged': _verify_staged,
        }
        function = functions[action]
        print(function(base, candidate_name, original_identity, candidate_identity, lock))
        return 0
    raise SystemExit(2)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError):
        raise SystemExit(1) from None
