import argparse
import os
import secrets
import stat
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
OUTPUT_DIR_ENV = 'GOOGLE_SEARCH_OUTPUT_DIR'
DIRECTORY_OPEN_FLAGS = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
SYSTEM_TMP_ROOTS = {Path('/tmp'), Path('/var/tmp')}
CREATABLE_SKILL_ROOTS = {BASE_DIR / 'runtime', BASE_DIR / 'output'}
BIDI_CONTROL_CODEPOINTS = frozenset({
    0x061C,
    0x200E,
    0x200F,
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
})


class OutputSecurityError(ValueError):
    pass


def _expand_user_path(value):
    try:
        return Path(value).expanduser()
    except RuntimeError as error:
        raise OutputSecurityError('output path contains an unresolvable user home') from error


def _validate_output_path_characters(value):
    try:
        text = os.fspath(value)
    except TypeError:
        raise OutputSecurityError('output path must be text') from None
    if not isinstance(text, str):
        raise OutputSecurityError('output path must be text')
    for character in text:
        codepoint = ord(character)
        if (
            codepoint < 0x20
            or 0x7F <= codepoint <= 0x9F
            or 0xD800 <= codepoint <= 0xDFFF
            or codepoint in {0x2028, 0x2029}
            or codepoint in BIDI_CONTROL_CODEPOINTS
        ):
            raise OutputSecurityError('output path contains unsafe control or directional characters')
    return text


def _validate_directory_metadata(path, metadata):
    if path in SYSTEM_TMP_ROOTS:
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o1777:
            raise OutputSecurityError('system temporary root must be root-owned with exact mode 01777')
    elif metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
        raise OutputSecurityError('configured output root has unsafe ownership or permissions')


def _validate_ancestor_chain(path, *, child_owner=None):
    current = Path(path)
    if child_owner is None:
        try:
            child_owner = os.lstat(current).st_uid
        except OSError as error:
            raise OutputSecurityError('configured output root has an unsafe ancestor') from error

    allowed_owners = {os.geteuid(), 0}
    while True:
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise OutputSecurityError('configured output root has an unsafe ancestor') from error
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            raise OutputSecurityError('configured output root has a symlink ancestor')
        if not stat.S_ISDIR(metadata.st_mode):
            raise OutputSecurityError('configured output root has a non-directory ancestor')
        if metadata.st_uid not in allowed_owners:
            raise OutputSecurityError('configured output root has an unsafe ancestor')
        if mode & 0o022:
            if not (
                current in SYSTEM_TMP_ROOTS
                and metadata.st_uid == 0
                and mode == 0o1777
                and child_owner in allowed_owners
            ):
                raise OutputSecurityError('configured output root has an unsafe ancestor')
        if current == current.parent:
            return
        child_owner = metadata.st_uid
        current = current.parent


def _is_relative_to(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _configured_roots():
    roots = [
        Path('/tmp'),
        Path('/var/tmp'),
        BASE_DIR / 'runtime',
        BASE_DIR / 'output',
    ]
    configured = os.environ.get(OUTPUT_DIR_ENV)
    if configured:
        configured_path = _expand_user_path(configured)
        if not configured_path.is_absolute():
            raise OutputSecurityError(f'{OUTPUT_DIR_ENV} must be an absolute path')
        roots.append(configured_path)

    deduped = []
    for root in roots:
        lexical = Path(os.path.abspath(os.fspath(root)))
        if lexical not in deduped:
            deduped.append(lexical)
    return deduped


def _select_root(path):
    for root in _configured_roots():
        if _is_relative_to(path, root):
            return root
    raise OutputSecurityError(
        f'output path must be under the system temporary directory, skill runtime/output, or {OUTPUT_DIR_ENV}'
    )


def _open_directory_components(path):
    path = Path(path)
    if not path.is_absolute():
        raise OutputSecurityError('internal output directory path must be absolute')
    descriptor = os.open(path.anchor, DIRECTORY_OPEN_FLAGS)
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(part, DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _create_skill_root(root):
    parent_fd = _open_directory_components(root.parent)
    try:
        try:
            os.mkdir(root.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    finally:
        os.close(parent_fd)


def _ensure_safe_parent(parent, root):
    try:
        os.lstat(root)
    except FileNotFoundError:
        if root in CREATABLE_SKILL_ROOTS:
            _create_skill_root(root)
        else:
            raise OutputSecurityError('configured output root must already exist')
    _validate_ancestor_chain(root)
    if not _is_relative_to(parent, root):
        raise OutputSecurityError('output parent escapes the allowed root')

    try:
        descriptor = _open_directory_components(root)
    except OSError as error:
        raise OutputSecurityError('configured output root contains a symlink or non-directory') from error
    try:
        root_stat = os.fstat(descriptor)
        _validate_directory_metadata(root, root_stat)

        for part in parent.relative_to(root).parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(part, DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            current_stat = os.fstat(descriptor)
            if current_stat.st_uid != os.geteuid() or current_stat.st_mode & 0o022:
                raise OutputSecurityError('output parent has unsafe ownership or permissions')
    except OSError as error:
        raise OutputSecurityError('output parent contains a symlink or non-directory') from error
    finally:
        os.close(descriptor)

    return parent


def normalize_output_target(save_path):
    raw_value = _validate_output_path_characters(save_path)
    if not raw_value or raw_value.isspace():
        raise OutputSecurityError('output path must not be empty')
    raw_path = _expand_user_path(raw_value)
    _validate_output_path_characters(raw_path)
    relative_root = None
    if not raw_path.is_absolute():
        relative_root = Path(os.path.abspath(os.fspath(BASE_DIR / 'output')))
        raw_path = relative_root / raw_path

    lexical_path = Path(os.path.abspath(os.fspath(raw_path)))
    _validate_output_path_characters(lexical_path)
    if relative_root is not None and not _is_relative_to(lexical_path, relative_root):
        raise OutputSecurityError('relative output path escapes the skill output directory')
    root = _select_root(lexical_path)
    if lexical_path == root or not _is_relative_to(lexical_path.parent, root):
        raise OutputSecurityError('output path must name a file')
    if not lexical_path.name or lexical_path.name in {'.', '..'}:
        raise OutputSecurityError('output path must name a file')
    return lexical_path


def _preflight_safe_parent(target, root):
    parent = target.parent
    if not _is_relative_to(parent, root):
        raise OutputSecurityError('output parent escapes the allowed root')

    try:
        os.lstat(root)
    except FileNotFoundError:
        if root not in CREATABLE_SKILL_ROOTS:
            raise OutputSecurityError('configured output root must already exist') from None
        _validate_ancestor_chain(root.parent, child_owner=os.geteuid())
        try:
            descriptor = _open_directory_components(root.parent)
        except OSError as error:
            raise OutputSecurityError(
                'configured output root parent contains a symlink or non-directory'
            ) from error
        else:
            os.close(descriptor)
        return
    except OSError as error:
        raise OutputSecurityError('configured output root could not be securely inspected') from error

    _validate_ancestor_chain(root)
    try:
        descriptor = _open_directory_components(root)
    except OSError as error:
        raise OutputSecurityError('configured output root contains a symlink or non-directory') from error
    try:
        _validate_directory_metadata(root, os.fstat(descriptor))
        for part in parent.relative_to(root).parts:
            try:
                next_descriptor = os.open(part, DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                return
            os.close(descriptor)
            descriptor = next_descriptor
            current_stat = os.fstat(descriptor)
            if current_stat.st_uid != os.geteuid() or current_stat.st_mode & 0o022:
                raise OutputSecurityError('output parent has unsafe ownership or permissions')
        _validate_existing_target(descriptor, target.name)
    except OutputSecurityError:
        raise
    except OSError as error:
        raise OutputSecurityError('output parent contains a symlink or non-directory') from error
    finally:
        os.close(descriptor)


def preflight_output_path(save_path):
    target = normalize_output_target(save_path)
    root = _select_root(target)
    _preflight_safe_parent(target, root)
    return target


def _resolve_target(save_path):
    target = normalize_output_target(save_path)
    root = _select_root(target)
    parent = _ensure_safe_parent(target.parent, root)
    target = parent / target.name
    return target, root


def _validate_existing_target(directory_fd, target_name):
    try:
        target_stat = os.stat(target_name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(target_stat.st_mode) or not stat.S_ISREG(target_stat.st_mode):
        raise OutputSecurityError('output target must be a regular, non-symlink file')
    if target_stat.st_uid != os.geteuid() or target_stat.st_nlink != 1:
        raise OutputSecurityError('output target has unsafe ownership or hard links')


def _validate_open_parent(directory_fd, target_parent, root):
    directory_stat = os.fstat(directory_fd)
    path_stat = os.stat(target_parent, follow_symlinks=False)
    if (directory_stat.st_dev, directory_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
        raise OutputSecurityError('output parent changed during validation')
    if target_parent in SYSTEM_TMP_ROOTS:
        _validate_directory_metadata(target_parent, directory_stat)
    elif directory_stat.st_uid != os.geteuid() or directory_stat.st_mode & 0o022:
        raise OutputSecurityError('output parent has unsafe ownership or permissions')
    resolved_parent = Path(os.path.realpath(f'/proc/self/fd/{directory_fd}'))
    if not _is_relative_to(resolved_parent, root):
        raise OutputSecurityError('output parent moved outside the allowed root')
    return directory_stat


def _write_all(descriptor, payload):
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError('short write')
        written += count


def secure_write_text(text, save_path):
    if not isinstance(text, str):
        raise TypeError('text must be a string')
    encoded = text.encode('utf-8')
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise OutputSecurityError(f'output exceeds the {MAX_OUTPUT_BYTES}-byte limit')

    target, root = _resolve_target(save_path)
    directory_fd = None
    temp_fd = None
    temp_name = None
    try:
        directory_fd = _open_directory_components(target.parent)
        directory_stat = _validate_open_parent(directory_fd, target.parent, root)
        _validate_existing_target(directory_fd, target.name)

        temp_name = f'.google-search-{secrets.token_hex(16)}.tmp'
        temp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        temp_fd = os.open(temp_name, temp_flags, 0o600, dir_fd=directory_fd)
        os.fchmod(temp_fd, 0o600)
        _write_all(temp_fd, encoded)
        os.fsync(temp_fd)
        temp_stat = os.fstat(temp_fd)

        _validate_existing_target(directory_fd, target.name)

        latest_parent_stat = os.stat(target.parent, follow_symlinks=False)
        if (directory_stat.st_dev, directory_stat.st_ino) != (
            latest_parent_stat.st_dev, latest_parent_stat.st_ino
        ):
            raise OutputSecurityError('output parent changed during the write')
        resolved_parent = Path(os.path.realpath(f'/proc/self/fd/{directory_fd}'))
        if not _is_relative_to(resolved_parent, root):
            raise OutputSecurityError('output parent moved outside the allowed root')

        os.replace(temp_name, target.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temp_name = None
        installed_stat = os.stat(target.name, dir_fd=directory_fd, follow_symlinks=False)
        if (installed_stat.st_dev, installed_stat.st_ino) != (temp_stat.st_dev, temp_stat.st_ino):
            raise OutputSecurityError('output target changed immediately after installation')
        os.fsync(directory_fd)
        return target
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if directory_fd is not None:
            os.close(directory_fd)


def atomic_write_text(path, text, mode=0o600):
    if mode != 0o600:
        raise OutputSecurityError('secure text output only supports mode 0600')
    return secure_write_text(text, path)


def build_parser():
    parser = argparse.ArgumentParser(
        description='Atomically write UTF-8 stdin to an approved output path.', allow_abbrev=False,
    )
    parser.add_argument('--path', required=True, help='Destination path')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    payload = sys.stdin.buffer.read(MAX_OUTPUT_BYTES + 1)
    if len(payload) > MAX_OUTPUT_BYTES:
        raise SystemExit(f'secure_io: input exceeds the {MAX_OUTPUT_BYTES}-byte limit')
    try:
        text = payload.decode('utf-8')
        secure_write_text(text, args.path)
    except (UnicodeDecodeError, OutputSecurityError, OSError) as error:
        raise SystemExit(f'secure_io: {error}') from None
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
