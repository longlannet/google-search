import argparse
import ast
import enum
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import os
import re
import stat
import sys
from pathlib import Path


MAX_RESULT_BYTES = 16 * 1024 * 1024
MAX_DEVELOPMENT_LOCK_BYTES = 4 * 1024 * 1024
MAX_RELEASE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_RELEASE_FILES = 10_000
MAX_RELEASE_SOURCE_BYTES = 64 * 1024 * 1024
PYTEST_SENTINEL = 'google-search-pytest-ok-v1'
KEYS_SENTINEL = 'google-search-keys-ok-v1'
RELEASE_SOURCE_SENTINEL = 'google-search-release-source-ok-v1'
RESULT_SENTINELS = {
    'smoke': 'google-search-smoke-result-ok-v1',
    'parsing': 'google-search-parsing-result-ok-v1',
    'full': 'google-search-full-result-ok-v1',
}
NEGATIVE_ENDPOINTS = (
    'arg-conflict-json-raw',
    'reviews-missing-id',
    'reviews-multiple-ids',
    'maps-reviews-all-pick-conflict',
    'webpage-missing-url',
    'num-nonpositive',
    'num-too-large',
    'page-nonpositive',
    'limit-nonpositive',
    'unknown-endpoint',
)
NEGATIVE_ENDPOINT_MESSAGES = {
    'arg-conflict-json-raw': '--json cannot be combined with --raw',
    'reviews-missing-id': 'reviews endpoint requires exactly one of: --place-id, --cid, or --fid',
    'reviews-multiple-ids': 'reviews endpoint requires exactly one of: --place-id, --cid, or --fid',
    'maps-reviews-all-pick-conflict': '--pick cannot be combined with --all for maps-reviews',
    'webpage-missing-url': 'webpage endpoint requires a URL',
    'num-nonpositive': 'num must be between 1 and 100',
    'num-too-large': 'num must be between 1 and 100',
    'page-nonpositive': 'page must be between 1 and 100',
    'limit-nonpositive': 'limit must be between 1 and 100',
    'unknown-endpoint': 'Unknown mode / endpoint',
}
FULL_ENDPOINTS = (
    'search',
    'images',
    'news',
    'autocomplete',
    'maps',
    'webpage',
    *NEGATIVE_ENDPOINTS,
    'patents',
    'lens',
    'videos',
    'places',
    'shopping',
    'scholar',
    'maps-reviews',
    'maps-reviews-pick2',
    'maps-reviews-all',
)
FULL_GROUPS = ['network-basic', 'parsing', 'network-full', 'workflows']
NETWORK_ENDPOINT_QUERIES = {
    'search': 'OpenClaw',
    'images': 'cat',
    'news': 'OpenAI',
    'autocomplete': 'openai',
    'maps': 'coffee shanghai',
    'webpage': 'https://openclaw.ai',
    'patents': 'OpenAI',
    'lens': (
        'https://upload.wikimedia.org/wikipedia/commons/4/47/'
        'PNG_transparency_demonstration_1.png'
    ),
    'videos': 'OpenAI',
    'places': 'coffee shanghai',
    'shopping': 'RTX 5090',
    'scholar': 'retrieval augmented generation',
}
NETWORK_LIST_KEYS = {
    'search': ('organic',),
    'images': ('images',),
    'news': ('news',),
    'autocomplete': ('suggestions',),
    'maps': ('places',),
    'patents': ('organic',),
    'videos': ('videos',),
    'places': ('places',),
    'shopping': ('shopping',),
    'scholar': ('organic',),
}
SHAPE_BOOLEAN_FIELDS = (
    'hasOrganic',
    'hasAnswerBox',
    'hasKnowledgeGraph',
    'hasCredits',
    'hasSearchParameters',
    'hasNonEmptyText',
)


class OnlineConfigError(ValueError):
    pass


def _stable_private_read(path, *, byte_limit, description):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError(f'{description} path must be absolute')
    stable_fields = (
        'st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink',
        'st_size', 'st_mtime_ns', 'st_ctime_ns',
    )
    try:
        before = path.lstat()
    except OSError:
        raise ValueError(f'{description} is missing or unsafe') from None
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_size > byte_limit
    ):
        raise ValueError(f'{description} metadata is unsafe')

    flags = (
        os.O_RDONLY
        | getattr(os, 'O_CLOEXEC', 0)
        | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_NONBLOCK', 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError(f'{description} cannot be opened safely') from None
    try:
        opened = os.fstat(descriptor)
        if any(getattr(before, field) != getattr(opened, field) for field in stable_fields):
            raise ValueError(f'{description} changed before it was opened')
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, byte_limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > byte_limit:
                raise ValueError(f'{description} is too large')
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
        raise ValueError(f'{description} changed while it was read')
    try:
        named_after = path.lstat()
    except OSError:
        raise ValueError(f'{description} pathname changed while it was read') from None
    if any(getattr(after, field) != getattr(named_after, field) for field in stable_fields):
        raise ValueError(f'{description} pathname changed while it was read')
    return b''.join(chunks)


def _release_manifest_records(path, description):
    data = _stable_private_read(
        path,
        byte_limit=MAX_RELEASE_MANIFEST_BYTES,
        description=description,
    )
    if not data or not data.endswith(b'\0'):
        raise ValueError(f'{description} is not a complete NUL-delimited manifest')
    records = data.split(b'\0')[:-1]
    if not records or len(records) > MAX_RELEASE_FILES:
        raise ValueError(f'{description} has an invalid entry count')
    return records


def _safe_release_name(raw_name):
    try:
        name = raw_name.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('release manifest contains a non-UTF-8 path') from None
    parts = name.split('/')
    if not name or name.startswith('/') or any(part in {'', '.', '..'} for part in parts):
        raise ValueError('release manifest contains an unsafe path')
    bidi_controls = {
        0x061C,
        0x200E,
        0x200F,
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    }
    for character in name:
        codepoint = ord(character)
        if (
            codepoint < 0x20
            or 0x7F <= codepoint <= 0x9F
            or 0xD800 <= codepoint <= 0xDFFF
            or codepoint in {0x2028, 0x2029}
            or codepoint in bidi_controls
        ):
            raise ValueError('release manifest contains a terminal-unsafe path')
    return name


def _parse_release_head(path):
    result = {}
    for record in _release_manifest_records(path, 'release HEAD manifest'):
        try:
            header, raw_name = record.split(b'\t', 1)
            mode, object_type, object_id = header.split(b' ')
        except ValueError:
            raise ValueError('release HEAD manifest is malformed') from None
        name = _safe_release_name(raw_name)
        if (
            object_type != b'blob'
            or mode not in {b'100644', b'100755'}
            or len(object_id) != 40
            or any(byte not in b'0123456789abcdef' for byte in object_id)
            or name in result
        ):
            raise ValueError('release HEAD manifest contains an unsafe entry')
        result[name] = (mode, object_id.decode('ascii'))
    return result


def _parse_release_index(path):
    result = {}
    for record in _release_manifest_records(path, 'release index manifest'):
        try:
            header, raw_name = record.split(b'\t', 1)
            mode, object_id, stage = header.split(b' ')
        except ValueError:
            raise ValueError('release index manifest is malformed') from None
        name = _safe_release_name(raw_name)
        if (
            stage != b'0'
            or mode not in {b'100644', b'100755'}
            or len(object_id) != 40
            or any(byte not in b'0123456789abcdef' for byte in object_id)
            or name in result
        ):
            raise ValueError('release index manifest contains an unsafe entry')
        result[name] = (mode, object_id.decode('ascii'))
    return result


def _validate_release_parent_chain(root, relative_name, expected_device):
    current = root
    for part in relative_name.split('/')[:-1]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            raise ValueError('release source parent is missing or unsafe') from None
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
            or metadata.st_dev != expected_device
        ):
            raise ValueError('release source parent metadata is unsafe')


def validate_release_source(base_dir, head_tree_path, index_stage_path):
    root = Path(base_dir)
    if not root.is_absolute():
        raise ValueError('release source root must be absolute')
    try:
        root = root.resolve(strict=True)
        root_metadata = root.lstat()
    except OSError:
        raise ValueError('release source root is missing or unsafe') from None
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or root_metadata.st_uid != os.geteuid()
        or root_metadata.st_mode & 0o022
    ):
        raise ValueError('release source root metadata is unsafe')

    head = _parse_release_head(head_tree_path)
    if _parse_release_index(index_stage_path) != head:
        raise ValueError('release index does not exactly match HEAD')

    stable_fields = (
        'st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink',
        'st_size', 'st_mtime_ns', 'st_ctime_ns',
    )
    total_size = 0
    for name, (git_mode, expected_object_id) in sorted(head.items()):
        _validate_release_parent_chain(root, name, root_metadata.st_dev)
        path = root.joinpath(*name.split('/'))
        try:
            before = path.lstat()
        except OSError:
            raise ValueError('release source file is missing or unsafe') from None
        expected_mode = 0o755 if git_mode == b'100755' else 0o644
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_dev != root_metadata.st_dev
            or stat.S_IMODE(before.st_mode) != expected_mode
        ):
            raise ValueError('release source file metadata is unsafe')
        total_size += before.st_size
        if total_size > MAX_RELEASE_SOURCE_BYTES:
            raise ValueError('release source content exceeds its byte limit')

        flags = (
            os.O_RDONLY
            | getattr(os, 'O_CLOEXEC', 0)
            | getattr(os, 'O_NOFOLLOW', 0)
            | getattr(os, 'O_NONBLOCK', 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise ValueError('release source file cannot be opened safely') from None
        try:
            opened = os.fstat(descriptor)
            if any(getattr(before, field) != getattr(opened, field) for field in stable_fields):
                raise ValueError('release source file changed before it was opened')
            digest = hashlib.sha1(
                f'blob {opened.st_size}\0'.encode('ascii'),
                usedforsecurity=False,
            )
            bytes_read = 0
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > opened.st_size:
                    raise ValueError('release source file grew while it was read')
                digest.update(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        try:
            named_after = path.lstat()
        except OSError:
            raise ValueError('release source pathname changed while it was read') from None
        if (
            bytes_read != opened.st_size
            or any(getattr(opened, field) != getattr(after, field) for field in stable_fields)
            or any(getattr(after, field) != getattr(named_after, field) for field in stable_fields)
            or digest.hexdigest() != expected_object_id
        ):
            raise ValueError('release source file does not match its committed blob')


def _canonical_distribution_name(value):
    return re.sub(r'[-_.]+', '-', str(value)).lower()


def _read_development_lock(base_dir):
    path = base_dir / 'requirements-dev.txt'
    stable_fields = (
        'st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
    )
    try:
        before = path.lstat()
    except OSError:
        raise ValueError('development lock is missing or unsafe') from None
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid not in {os.geteuid(), 0}
        or before.st_mode & 0o022
        or before.st_nlink != 1
        or before.st_size > MAX_DEVELOPMENT_LOCK_BYTES
    ):
        raise ValueError('development lock metadata is unsafe')

    flags = (
        os.O_RDONLY
        | getattr(os, 'O_CLOEXEC', 0)
        | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_NONBLOCK', 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ValueError('development lock cannot be opened safely') from None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid not in {os.geteuid(), 0}
            or opened.st_mode & 0o022
            or opened.st_nlink != 1
            or opened.st_size > MAX_DEVELOPMENT_LOCK_BYTES
            or any(getattr(before, field) != getattr(opened, field) for field in stable_fields)
        ):
            raise ValueError('development lock changed before it was opened')
        chunks = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(64 * 1024, MAX_DEVELOPMENT_LOCK_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_DEVELOPMENT_LOCK_BYTES:
                raise ValueError('development lock is too large')
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
        raise ValueError('development lock changed while it was read')
    try:
        named_after = path.lstat()
    except OSError:
        raise ValueError('development lock pathname changed while it was read') from None
    if any(getattr(after, field) != getattr(named_after, field) for field in stable_fields):
        raise ValueError('development lock pathname changed while it was read')
    try:
        return b''.join(chunks).decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('development lock is not UTF-8') from None


def _numeric_version(value):
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', value):
        raise ValueError('development lock contains an unsupported marker version')
    return tuple(int(part) for part in value.split('.'))


def _compare_marker_values(left, operator, right, *, numeric):
    if numeric:
        left = _numeric_version(left)
        right = _numeric_version(right)
        width = max(len(left), len(right))
        left += (0,) * (width - len(left))
        right += (0,) * (width - len(right))
    comparisons = {
        '==': left == right,
        '!=': left != right,
        '<': left < right,
        '<=': left <= right,
        '>': left > right,
        '>=': left >= right,
    }
    return comparisons[operator]


def _marker_applies(marker):
    match = re.fullmatch(
        r"(python_full_version|python_version|sys_platform|os_name)\s*"
        r"(==|!=|<=|>=|<|>)\s*(['\"])([^'\"]+)\3",
        marker.strip(),
    )
    if match is None:
        raise ValueError('development lock contains an unsupported environment marker')
    name, operator, _, required = match.groups()
    current = {
        'python_full_version': '.'.join(str(part) for part in sys.version_info[:3]),
        'python_version': '.'.join(str(part) for part in sys.version_info[:2]),
        'sys_platform': sys.platform,
        'os_name': os.name,
    }[name]
    numeric = name in {'python_full_version', 'python_version'}
    if not numeric and operator not in {'==', '!='}:
        raise ValueError('development lock contains an unsupported string marker comparison')
    return _compare_marker_values(current, operator, required, numeric=numeric)


def _expected_development_distributions(base_dir):
    expected = {}
    requirement_pattern = re.compile(
        r'([A-Za-z0-9][A-Za-z0-9_.-]*)=='
        r'([A-Za-z0-9][A-Za-z0-9.!+_-]*)',
    )
    for raw_line in _read_development_lock(base_dir).splitlines():
        line = raw_line.strip()
        if not line or line.startswith(('#', '--', '-r ', '-c ')):
            continue
        if line.endswith('\\'):
            line = line[:-1].rstrip()
        line = re.sub(r'\s+--hash=sha256:[0-9A-Fa-f]{64}(?:\s+.*)?$', '', line)
        requirement, separator, marker = line.partition(';')
        match = requirement_pattern.fullmatch(requirement.strip())
        if match is None:
            raise ValueError('development lock contains an invalid or unpinned requirement')
        if separator and not _marker_applies(marker):
            continue
        name = _canonical_distribution_name(match.group(1))
        if name in expected:
            raise ValueError('development lock contains a duplicate applicable requirement')
        expected[name] = match.group(2)
    if not expected:
        raise ValueError('development lock contains no applicable requirements')
    return expected


def _verify_development_distribution_versions(base_dir, required_pytest_version):
    expected = _expected_development_distributions(base_dir)
    if expected.get('pytest') != required_pytest_version:
        raise ValueError('pytest version does not match the development lock')
    for distribution, required_version in sorted(expected.items()):
        try:
            actual_version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            raise ValueError(f'development distribution is missing: {distribution}') from None
        if actual_version != required_version:
            raise ValueError(f'development distribution version mismatch: {distribution}')
    return expected


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_int(value):
    return _is_int(value) and value >= 1


def _read_private_json(path):
    before = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('result is not a regular file')
    flags = (
        os.O_RDONLY
        | getattr(os, 'O_CLOEXEC', 0)
        | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_NONBLOCK', 0)
    )
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError('result is not a regular file')
        if (metadata.st_dev, metadata.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('result changed before it was opened')
        if metadata.st_uid != os.geteuid() or metadata.st_nlink != 1 or metadata.st_mode & 0o077:
            raise ValueError('result metadata is unsafe')
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_RESULT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_RESULT_BYTES:
                raise ValueError('result is too large')
        payload = b''.join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    stable_fields = (
        'st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
    )
    if any(getattr(metadata, field) != getattr(after, field) for field in stable_fields):
        raise ValueError('result changed while it was read')
    named_after = os.stat(path, follow_symlinks=False)
    if any(getattr(after, field) != getattr(named_after, field) for field in stable_fields):
        raise ValueError('result pathname changed while it was read')
    try:
        value = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError('result is not valid JSON') from None
    if not isinstance(value, dict):
        raise ValueError('result must be a JSON object')
    return value


def _validate_success_envelope(payload):
    if payload.get('ok') is not True or payload.get('trust') != 'untrusted_external_content':
        raise ValueError('result does not report the success protocol')
    if 'error' in payload:
        raise ValueError('successful result unexpectedly contains an error')


def _validate_smoke_result(payload):
    _validate_success_envelope(payload)
    key_count = payload.get('keyCount')
    key_slot = payload.get('keySlot')
    organic_count = payload.get('organicCount')
    shape = payload.get('shape')
    if (
        payload.get('kind') != 'smoke-test'
        or payload.get('endpoint') != 'search'
        or payload.get('query') != 'OpenClaw'
        or not _positive_int(key_count)
        or not _positive_int(key_slot)
        or key_slot > key_count
        or not _positive_int(organic_count)
        or not isinstance(shape, dict)
    ):
        raise ValueError('smoke result is incomplete')
    top_level_keys = shape.get('topLevelKeys')
    list_lengths = shape.get('listLengths')
    organic_list_length = list_lengths.get('organic') if isinstance(list_lengths, dict) else None
    if (
        shape.get('hasOrganic') is not True
        or not isinstance(top_level_keys, list)
        or not all(isinstance(item, str) for item in top_level_keys)
        or 'organic' not in top_level_keys
        or not isinstance(list_lengths, dict)
        or not _positive_int(organic_list_length)
        or organic_list_length != organic_count
    ):
        raise ValueError('smoke result shape does not prove organic results')


def _validate_response_shape(shape):
    if not isinstance(shape, dict):
        raise ValueError('endpoint result is missing its response shape')
    top_level_keys = shape.get('topLevelKeys')
    list_lengths = shape.get('listLengths')
    if (
        not isinstance(top_level_keys, list)
        or any(not isinstance(item, str) for item in top_level_keys)
        or top_level_keys != sorted(set(top_level_keys))
        or not isinstance(list_lengths, dict)
        or any(not isinstance(name, str) for name in list_lengths)
        or any(not _is_int(count) or count < 0 for count in list_lengths.values())
        or not set(list_lengths).issubset(top_level_keys)
        or any(not isinstance(shape.get(name), bool) for name in SHAPE_BOOLEAN_FIELDS)
    ):
        raise ValueError('endpoint response shape is malformed')
    if 'organic' in list_lengths and shape['hasOrganic'] is not (list_lengths['organic'] > 0):
        raise ValueError('endpoint organic shape is inconsistent')
    return top_level_keys, list_lengths


def _require_list_shape(shape, names, minimum=0):
    _, list_lengths = _validate_response_shape(shape)
    matching_counts = [list_lengths[name] for name in names if name in list_lengths]
    if not matching_counts or max(matching_counts) < minimum:
        raise ValueError('endpoint response shape is missing its expected list')
    return max(matching_counts)


def _validate_parsing_endpoint(name, result):
    if (
        result.get('ok') is not True
        or result.get('expectedError') != 'UsageError'
        or result.get('message') != NEGATIVE_ENDPOINT_MESSAGES[name]
        or 'error' in result
    ):
        raise ValueError(f'parsing result is incomplete: {name}')


def _validate_network_endpoint(name, result, key_count):
    if (
        result.get('ok') is not True
        or result.get('query') != NETWORK_ENDPOINT_QUERIES[name]
        or not _positive_int(result.get('keySlot'))
        or result['keySlot'] > key_count
        or 'error' in result
    ):
        raise ValueError(f'network result is incomplete: {name}')
    if name == 'webpage':
        shape = result.get('shape')
        top_level_keys, _ = _validate_response_shape(shape)
        if 'text' not in top_level_keys or shape['hasNonEmptyText'] is not True:
            raise ValueError('webpage result does not prove a non-empty text response')
    elif name == 'lens':
        _require_list_shape(
            result.get('shape'),
            ('organic', 'visualMatches', 'similarImages', 'images'),
            minimum=1,
        )
    else:
        _require_list_shape(result.get('shape'), NETWORK_LIST_KEYS[name], minimum=1)


def _validate_selected_place(value):
    if not isinstance(value, dict):
        raise ValueError('workflow result is missing its selected place')
    identifiers = (value.get('placeId'), value.get('cid'), value.get('fid'))
    if not any(isinstance(identifier, str) and identifier for identifier in identifiers):
        raise ValueError('workflow selected place has no review identifier')


def _validate_workflow_endpoint(name, result):
    expected_pick = 2 if name == 'maps-reviews-pick2' else 1
    if (
        result.get('ok') is not True
        or result.get('query') != 'coffee shanghai'
        or result.get('error') is not None
    ):
        raise ValueError(f'workflow result is incomplete: {name}')
    maps_count = _require_list_shape(result.get('mapsShape'), ('places',), minimum=1)
    if name == 'maps-reviews-all':
        result_count = result.get('resultCount')
        expected_count = min(maps_count, 3)
        review_shapes = result.get('reviewShapes')
        if (
            result.get('allSucceeded') is not True
            or not _positive_int(result_count)
            or result_count != expected_count
            or not _is_int(result.get('failedCount'))
            or result.get('failedCount') != 0
            or not isinstance(review_shapes, list)
            or len(review_shapes) != result_count
        ):
            raise ValueError('maps-reviews-all result counts are inconsistent')
        for review_shape in review_shapes:
            _require_list_shape(review_shape, ('reviews', 'organic'))
        return
    if result.get('pick') != expected_pick or maps_count < expected_pick:
        raise ValueError(f'workflow pick result is inconsistent: {name}')
    _validate_selected_place(result.get('selectedPlace'))
    _require_list_shape(result.get('reviewsShape'), ('reviews', 'organic'))


def _validate_selfcheck_result(payload, expected):
    _validate_success_envelope(payload)
    if (
        payload.get('kind') != 'selfcheck'
        or not _is_int(payload.get('exitCode'))
        or payload.get('exitCode') != 0
        or payload.get('errors') != []
        or payload.get('failureKinds') != []
    ):
        raise ValueError('selfcheck result is incomplete')

    if expected == 'parsing':
        expected_endpoints = NEGATIVE_ENDPOINTS
        if (
            payload.get('mode') != 'group'
            or payload.get('selectedGroups') != ['parsing']
            or not _is_int(payload.get('keyCount'))
            or payload.get('keyCount') != 0
        ):
            raise ValueError('parsing selfcheck mode mismatch')
    elif expected == 'full':
        expected_endpoints = FULL_ENDPOINTS
        if (
            payload.get('mode') != 'full'
            or payload.get('selectedGroups') != FULL_GROUPS
            or not _positive_int(payload.get('keyCount'))
        ):
            raise ValueError('full selfcheck mode mismatch')
    else:
        raise ValueError('unknown selfcheck result protocol')

    endpoints_tested = payload.get('endpointsTested')
    results = payload.get('results')
    if endpoints_tested != list(expected_endpoints):
        raise ValueError('selfcheck endpoint sentinel mismatch')
    if not isinstance(results, dict) or set(results) != set(expected_endpoints):
        raise ValueError('selfcheck result keys do not match the endpoint sentinel')
    for name in expected_endpoints:
        result = results[name]
        if not isinstance(result, dict):
            raise ValueError('selfcheck result contains an incomplete endpoint')
        if name in NEGATIVE_ENDPOINTS:
            _validate_parsing_endpoint(name, result)
        elif name in NETWORK_ENDPOINT_QUERIES:
            _validate_network_endpoint(name, result, payload['keyCount'])
        elif name in {'maps-reviews', 'maps-reviews-pick2', 'maps-reviews-all'}:
            _validate_workflow_endpoint(name, result)
        else:
            raise ValueError(f'unknown endpoint result protocol: {name}')


def validate_result(payload, expected):
    if expected == 'smoke':
        _validate_smoke_result(payload)
    elif expected in {'parsing', 'full'}:
        _validate_selfcheck_result(payload, expected)
    else:
        raise ValueError('unknown result protocol')


class _PytestCompletionPlugin:
    def __init__(self, expected_files):
        self.expected_files = frozenset(expected_files)
        self.collection_calls = 0
        self.sessionfinish_calls = 0
        self.collected_files = frozenset()
        self.collected_nodeids = ()
        self.finished_nodeids = []
        self.deselected_nodeids = []
        self.exit_status = None

    def pytest_collection_finish(self, session):
        self.collection_calls += 1
        items = tuple(session.items)
        self.collected_nodeids = tuple(item.nodeid for item in items)
        self.collected_files = frozenset(Path(item.path).resolve() for item in items)

    def pytest_deselected(self, items):
        self.deselected_nodeids.extend(item.nodeid for item in items)

    def pytest_runtest_logfinish(self, nodeid, location):
        del location
        self.finished_nodeids.append(nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        del session
        self.sessionfinish_calls += 1
        self.exit_status = exitstatus

    def validate(self, pytest_module, returned_exit_code):
        if not isinstance(returned_exit_code, pytest_module.ExitCode):
            raise ValueError('pytest.main returned an invalid exit-code type')
        if returned_exit_code is not pytest_module.ExitCode.OK:
            raise ValueError('pytest suite failed')
        if self.collection_calls != 1 or self.sessionfinish_calls != 1:
            raise ValueError('pytest lifecycle hooks did not complete exactly once')
        if self.exit_status != pytest_module.ExitCode.OK:
            raise ValueError('pytest sessionfinish did not report success')
        if self.deselected_nodeids:
            raise ValueError('pytest deselected tests')
        if not self.collected_nodeids or len(set(self.collected_nodeids)) != len(self.collected_nodeids):
            raise ValueError('pytest collected no tests or duplicate node IDs')
        if self.collected_files != self.expected_files:
            raise ValueError('pytest did not collect every expected test file')
        if len(self.finished_nodeids) != len(self.collected_nodeids):
            raise ValueError('pytest did not finish every collected test')
        if set(self.finished_nodeids) != set(self.collected_nodeids):
            raise ValueError('pytest completion node IDs do not match collection')


def _verify_pytest_distribution(required_version):
    pytest_module = importlib.import_module('pytest')
    distribution = importlib.metadata.distribution('pytest')
    if _canonical_distribution_name(distribution.metadata.get('Name')) != 'pytest':
        raise ValueError('pytest distribution name mismatch')
    if distribution.version != required_version or getattr(pytest_module, '__version__', None) != required_version:
        raise ValueError('pytest version mismatch')

    package_distributions = importlib.metadata.packages_distributions().get('pytest', [])
    if 'pytest' not in {_canonical_distribution_name(item) for item in package_distributions}:
        raise ValueError('pytest import is not mapped to the pytest distribution')
    distribution_root = Path(distribution.locate_file('')).resolve()
    module_path = Path(pytest_module.__file__).resolve()
    if not module_path.is_relative_to(distribution_root):
        raise ValueError('pytest module is outside its distribution root')

    required_files = {
        'pytest/__init__.py',
        '_pytest/config/__init__.py',
        '_pytest/main.py',
        '_pytest/nodes.py',
    }
    distribution_files = {str(path).replace(os.sep, '/') for path in (distribution.files or ())}
    if not required_files.issubset(distribution_files):
        raise ValueError('pytest distribution file list is incomplete')
    if not any(path.endswith('.dist-info/RECORD') for path in distribution_files):
        raise ValueError('pytest distribution RECORD is missing')

    expected_modules = {
        'main': '_pytest.config',
        'Config': '_pytest.config',
        'Session': '_pytest.main',
        'Item': '_pytest.nodes',
        'ExitCode': 'pytest',
    }
    for name, module_name in expected_modules.items():
        value = getattr(pytest_module, name, None)
        if value is None or getattr(value, '__module__', None) != module_name:
            raise ValueError(f'pytest API mismatch: {name}')
    if not callable(pytest_module.main):
        raise ValueError('pytest.main is not callable')
    signature = inspect.signature(pytest_module.main)
    if list(signature.parameters)[:2] != ['args', 'plugins']:
        raise ValueError('pytest.main signature mismatch')
    main_source = inspect.getsourcefile(pytest_module.main)
    if main_source is None or not Path(main_source).resolve().is_relative_to(distribution_root):
        raise ValueError('pytest.main implementation is outside its distribution')
    if not isinstance(pytest_module.ExitCode, type) or not issubclass(pytest_module.ExitCode, enum.IntEnum):
        raise ValueError('pytest.ExitCode is not an IntEnum')
    if pytest_module.ExitCode.OK.value != 0:
        raise ValueError('pytest success exit code mismatch')
    return pytest_module


def _validate_tree_entry(path, metadata, owner_ids, device, expect_directory):
    expected_type = stat.S_ISDIR if expect_directory else stat.S_ISREG
    if not expected_type(metadata.st_mode):
        raise ValueError('tests tree contains a symlink or special file')
    if metadata.st_uid not in owner_ids or metadata.st_mode & 0o022:
        raise ValueError('tests tree has unsafe ownership or permissions')
    if metadata.st_dev != device:
        raise ValueError('tests tree crosses a filesystem boundary')
    if not expect_directory and metadata.st_nlink != 1:
        raise ValueError('tests tree contains a hard-linked file')


def _expected_test_files(base_dir):
    base_dir = Path(base_dir).resolve(strict=True)
    tests_dir = base_dir / 'tests'
    try:
        root_metadata = tests_dir.lstat()
    except OSError:
        raise ValueError('tests directory is missing or unsafe')
    owner_ids = {os.geteuid(), 0}
    device = root_metadata.st_dev
    _validate_tree_entry(tests_dir, root_metadata, owner_ids, device, expect_directory=True)

    def walk_error(error):
        raise ValueError('tests tree cannot be traversed') from error

    for root, directory_names, file_names in os.walk(tests_dir, topdown=True, followlinks=False, onerror=walk_error):
        root_path = Path(root)
        _validate_tree_entry(root_path, root_path.lstat(), owner_ids, device, expect_directory=True)
        for name in directory_names:
            path = root_path / name
            _validate_tree_entry(path, path.lstat(), owner_ids, device, expect_directory=True)
        for name in file_names:
            path = root_path / name
            _validate_tree_entry(path, path.lstat(), owner_ids, device, expect_directory=False)

    paths = sorted(tests_dir.rglob('test_*.py'))
    if not paths:
        raise ValueError('no test files found')
    expected = set()
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError('test file is missing or unsafe')
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(tests_dir):
            raise ValueError('test file escapes the tests directory')
        expected.add(resolved)
    return base_dir, tests_dir, expected


def run_pytest_suite(base_dir, required_version):
    base_dir, tests_dir, expected_files = _expected_test_files(base_dir)
    _verify_development_distribution_versions(base_dir, required_version)
    pytest_module = _verify_pytest_distribution(required_version)
    plugin = _PytestCompletionPlugin(expected_files)
    args = [
        str(tests_dir),
        '-c',
        os.devnull,
        f'--rootdir={base_dir}',
        '-p',
        'no:terminal',
        '-p',
        'no:cacheprovider',
        '--noconftest',
        '--import-mode=importlib',
    ]
    previous_directory = Path.cwd()
    try:
        os.chdir(base_dir)
        try:
            exit_code = pytest_module.main(args=args, plugins=[plugin])
        finally:
            os.chdir(previous_directory)
    except BaseException as error:
        raise ValueError(f'pytest.main raised {type(error).__name__}') from None
    plugin.validate(pytest_module, exit_code)


def validate_online_keys(client_path):
    client_path = Path(client_path)
    if client_path.name != 'client.py' or client_path.is_symlink() or not client_path.is_file():
        raise ValueError('client module path is unsafe')
    client_path = client_path.resolve(strict=True)
    scripts_dir = client_path.parent
    module_name = '_google_search_check_client'
    spec = importlib.util.spec_from_file_location(module_name, client_path)
    if spec is None or spec.loader is None:
        raise ValueError('client module cannot be loaded')
    module = importlib.util.module_from_spec(spec)
    previous_path = list(sys.path)
    sys.modules.pop(module_name, None)
    try:
        sys.path.insert(0, str(scripts_dir))
        try:
            spec.loader.exec_module(module)
        except Exception:
            raise ValueError('client module could not be imported') from None
        load_api_keys = getattr(module, 'load_api_keys', None)
        config_error = getattr(module, 'SerperConfigError', None)
        if not callable(load_api_keys) or not isinstance(config_error, type) or not issubclass(config_error, Exception):
            raise ValueError('client configuration API is incomplete')
        try:
            keys = load_api_keys()
        except config_error:
            raise OnlineConfigError('API key configuration is invalid') from None
        except Exception:
            raise ValueError('client configuration API failed unexpectedly') from None
    finally:
        sys.path[:] = previous_path
        sys.modules.pop(module_name, None)
    if not isinstance(keys, list) or any(not isinstance(key, str) or not key for key in keys):
        raise ValueError('client configuration API returned malformed keys')
    if not keys:
        raise OnlineConfigError('API key configuration is empty or invalid')


def validate_python_ast(base_dir):
    base_dir = Path(base_dir).resolve(strict=True)
    if base_dir != Path(__file__).resolve(strict=True).parent.parent:
        raise ValueError('AST base directory does not match the skill root')
    paths = sorted((base_dir / 'scripts').glob('*.py')) + sorted(
        (base_dir / 'tests').glob('*.py')
    )
    if not paths:
        raise ValueError('no Python sources were found')
    for path in paths:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in {0, os.geteuid()}
            or metadata.st_mode & 0o022
            or metadata.st_nlink != 1
        ):
            raise ValueError('Python source has unsafe metadata')
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    print(f'AST OK: {len(paths)} files')


def build_parser():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest='command', required=True)

    pytest_parser = commands.add_parser('pytest', allow_abbrev=False)
    pytest_parser.add_argument('--base-dir', required=True)
    pytest_parser.add_argument('--required-version', required=True)

    result_parser = commands.add_parser('result', allow_abbrev=False)
    result_parser.add_argument('--path', required=True)
    result_parser.add_argument('--expected', choices=tuple(RESULT_SENTINELS), required=True)

    keys_parser = commands.add_parser('keys', allow_abbrev=False)
    keys_parser.add_argument('--client-path', required=True)

    ast_parser = commands.add_parser('ast', allow_abbrev=False)
    ast_parser.add_argument('--base-dir', required=True)

    release_source_parser = commands.add_parser('release-source', allow_abbrev=False)
    release_source_parser.add_argument('--base-dir', required=True)
    release_source_parser.add_argument('head_tree')
    release_source_parser.add_argument('index_stage')
    return parser


def main(argv=None):
    options = build_parser().parse_args(argv)
    if options.command == 'pytest':
        run_pytest_suite(options.base_dir, options.required_version)
        print(PYTEST_SENTINEL)
    elif options.command == 'result':
        validate_result(_read_private_json(options.path), options.expected)
        print(RESULT_SENTINELS[options.expected])
    elif options.command == 'keys':
        validate_online_keys(options.client_path)
        print(KEYS_SENTINEL)
    elif options.command == 'ast':
        validate_python_ast(options.base_dir)
    elif options.command == 'release-source':
        validate_release_source(options.base_dir, options.head_tree, options.index_stage)
        print(RELEASE_SOURCE_SENTINEL)
    else:
        raise ValueError('unknown check protocol command')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except OnlineConfigError:
        raise SystemExit(2) from None
    except (ImportError, OSError, ValueError):
        raise SystemExit(1) from None
