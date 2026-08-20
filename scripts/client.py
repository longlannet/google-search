import json
import math
import os
import re
import signal
import stat
import sys
import threading
from pathlib import Path

import requests

from args import (
    BIDI_CONTROLS,
    MAX_IDENTIFIER_LENGTH,
    MAX_NUM,
    MAX_PAGE,
    MAX_QUERY_LENGTH,
    UsageError,
    validate_public_https_url,
)
from io_common import sanitize_external_data


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SCRIPT_DIR.parent / 'config'
ENV_FILE = CONFIG_DIR / 'serper.env'
RUNTIME_DIR = SCRIPT_DIR.parent / 'runtime'
RR_INDEX_FILE = RUNTIME_DIR / 'serper_rr.idx'
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 15
REQUEST_TIMEOUT = (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)
REQUEST_WALL_TIMEOUT_SECONDS = 30
RESPONSE_CLOSE_TIMEOUT_SECONDS = 1
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_JSON_NUMBER_CHARS = 1_024
MAX_CONFIG_BYTES = 16 * 1024
MAX_API_KEYS = 32
MIN_API_KEY_LENGTH = 12
MAX_API_KEY_LENGTH = 256
REDACTED_API_KEY = '[REDACTED_API_KEY]'
USER_AGENT = 'openclaw-skill-google-search'
DEBUG_RR_ENV = 'SERPER_DEBUG_RR'
KEY_PATTERN = re.compile(r'^[A-Za-z0-9._~+/=-]+$')
LOCALE_PATTERN = re.compile(r'^[A-Za-z0-9]{2,8}(?:-[A-Za-z0-9]{1,8})?$')
FAILOVER_HTTP_STATUSES = {401, 402, 403, 429}
CONFIG_STABLE_FIELDS = (
    'st_dev',
    'st_ino',
    'st_uid',
    'st_mode',
    'st_nlink',
    'st_size',
    'st_mtime_ns',
    'st_ctime_ns',
)
ENDPOINT_URLS = {
    'search': 'https://google.serper.dev/search',
    'images': 'https://google.serper.dev/images',
    'news': 'https://google.serper.dev/news',
    'videos': 'https://google.serper.dev/videos',
    'places': 'https://google.serper.dev/places',
    'maps': 'https://google.serper.dev/maps',
    'reviews': 'https://google.serper.dev/reviews',
    'autocomplete': 'https://google.serper.dev/autocomplete',
    'shopping': 'https://google.serper.dev/shopping',
    'scholar': 'https://google.serper.dev/scholar',
    'patents': 'https://google.serper.dev/patents',
    'webpage': 'https://scrape.serper.dev',
    'lens': 'https://google.serper.dev/lens',
}


class SerperAPIError(Exception):
    def __init__(self, message, kind='api'):
        super().__init__(message)
        self.kind = kind


class SerperConfigError(SerperAPIError):
    def __init__(self, message):
        super().__init__(message, kind='config')


class _RequestDeadlineExpired(Exception):
    pass


class _RequestDeadline:
    def __init__(self, seconds):
        self.seconds = seconds
        self.expired = False
        self._previous_handler = None
        self._armed = False

    def _handle_alarm(self, _signum, _frame):
        self.expired = True
        raise _RequestDeadlineExpired

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            raise SerperAPIError('Serper requests require the main thread for a bounded deadline', kind='network')
        if not hasattr(signal, 'SIGALRM') or not hasattr(signal, 'setitimer'):
            raise SerperAPIError('A bounded Serper request timer is unavailable on this platform', kind='network')
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        if previous_timer != (0.0, 0.0):
            raise SerperAPIError('An existing process timer prevents a bounded Serper request', kind='network')
        self._previous_handler = signal.getsignal(signal.SIGALRM)
        try:
            signal.signal(signal.SIGALRM, self._handle_alarm)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)
            self._armed = True
        except (OSError, ValueError):
            signal.signal(signal.SIGALRM, self._previous_handler)
            raise SerperAPIError('The bounded Serper request timer could not be armed', kind='network') from None
        return self

    def __exit__(self, _error_type, _error, _traceback):
        if self._armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._previous_handler)
            self._armed = False
        return False


_session = requests.Session()
_session.trust_env = False


def _rr_debug_enabled():
    return os.environ.get(DEBUG_RR_ENV, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _rr_debug(message):
    if _rr_debug_enabled():
        print(f'[serper-rr] {message}', file=sys.stderr)


def _strip_optional_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\'', '"'}:
        return value[1:-1]
    return value


def _validate_key(value, source_label):
    if not isinstance(value, str):
        raise SerperConfigError(f'Invalid API key in {source_label}: value must be ASCII text')
    try:
        value.encode('ascii')
    except UnicodeEncodeError:
        raise SerperConfigError(f'Invalid API key in {source_label}: value must be ASCII text') from None
    lowered = value.lower()
    placeholder_markers = (
        'your_serper_api_key', 'your_api_key', 'replace_me', 'replace-with',
        'placeholder', 'changeme', 'example_key',
    )
    if lowered.startswith(('replace', 'insert-key', 'enter-key')) or any(marker in lowered for marker in placeholder_markers):
        raise SerperConfigError(f'Invalid API key in {source_label}: placeholder values are not allowed')
    if not (MIN_API_KEY_LENGTH <= len(value) <= MAX_API_KEY_LENGTH):
        raise SerperConfigError(
            f'Invalid API key in {source_label}: length must be between {MIN_API_KEY_LENGTH} and {MAX_API_KEY_LENGTH}'
        )
    if not KEY_PATTERN.fullmatch(value):
        raise SerperConfigError(f'Invalid API key in {source_label}: unsupported characters')
    if value in REDACTED_API_KEY:
        raise SerperConfigError(f'Invalid API key in {source_label}: value conflicts with the redaction marker')
    return value


def _dedupe_and_bound_keys(entries):
    keys = []
    seen = set()
    for value, source_label in entries:
        key = _validate_key(value, source_label)
        if key not in seen:
            keys.append(key)
            seen.add(key)
        if len(keys) > MAX_API_KEYS:
            raise SerperConfigError(f'API key count exceeds the {MAX_API_KEYS}-key limit')
    return keys


def _parse_environment_keys():
    entries = []
    if 'SERPER_API_KEY' in os.environ:
        raw_value = _strip_optional_quotes(os.environ.get('SERPER_API_KEY', '').strip())
        if not raw_value:
            raise SerperConfigError('SERPER_API_KEY is present but empty')
        entries.append((raw_value, 'SERPER_API_KEY'))
    if 'SERPER_API_KEYS' in os.environ:
        raw_values = os.environ.get('SERPER_API_KEYS', '')
        pieces = [piece.strip() for line in raw_values.splitlines() for piece in line.split(',')]
        if not pieces or any(not piece for piece in pieces):
            raise SerperConfigError('SERPER_API_KEYS is present but contains an empty entry')
        entries.extend(
            (_strip_optional_quotes(piece), f'SERPER_API_KEYS entry {index}')
            for index, piece in enumerate(pieces, start=1)
        )
    return _dedupe_and_bound_keys(entries)


def _read_config_bytes():
    directory_flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    flags = (
        os.O_RDONLY
        | getattr(os, 'O_CLOEXEC', 0)
        | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_NONBLOCK', 0)
    )
    directory_descriptor = None
    try:
        directory_descriptor = os.open(ENV_FILE.parent, directory_flags)
        directory_metadata = os.fstat(directory_descriptor)
        directory_identity = (
            directory_metadata.st_dev,
            directory_metadata.st_ino,
            directory_metadata.st_uid,
            directory_metadata.st_mode,
        )
        if directory_metadata.st_uid not in {os.geteuid(), 0}:
            raise SerperConfigError('API key directory must be owned by the current user or root')
        if directory_metadata.st_mode & 0o022:
            raise SerperConfigError('API key directory must not be group- or world-writable')
        before = os.stat(ENV_FILE.name, dir_fd=directory_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise SerperConfigError('API key file cannot be opened safely')
        descriptor = os.open(ENV_FILE.name, flags, dir_fd=directory_descriptor)
    except FileNotFoundError:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        return None
    except SerperConfigError:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        raise
    except OSError:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        raise SerperConfigError('API key file cannot be opened safely') from None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SerperConfigError('API key file must be a regular file')
        if any(
            getattr(before, field) != getattr(opened, field)
            for field in CONFIG_STABLE_FIELDS
        ):
            raise SerperConfigError('API key file changed before it was opened')
        if opened.st_uid not in {os.geteuid(), 0} or opened.st_nlink != 1:
            raise SerperConfigError('API key file must be owned by the current user or root and have one hard link')
        if opened.st_mode & 0o077:
            raise SerperConfigError('API key file permissions are too broad; require mode 0600 or stricter')
        if opened.st_size > MAX_CONFIG_BYTES:
            raise SerperConfigError(f'API key file exceeds the {MAX_CONFIG_BYTES}-byte limit')
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(4096, MAX_CONFIG_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_CONFIG_BYTES:
                raise SerperConfigError(f'API key file exceeds the {MAX_CONFIG_BYTES}-byte limit')
        after = os.fstat(descriptor)
        named_after = os.stat(ENV_FILE.name, dir_fd=directory_descriptor, follow_symlinks=False)
        directory_after = os.fstat(directory_descriptor)
        directory_named_after = os.stat(ENV_FILE.parent, follow_symlinks=False)
        if any(
            getattr(opened, field) != getattr(after, field)
            for field in CONFIG_STABLE_FIELDS
        ):
            raise SerperConfigError('API key file changed while it was read')
        if any(
            getattr(after, field) != getattr(named_after, field)
            for field in CONFIG_STABLE_FIELDS
        ):
            raise SerperConfigError('API key file pathname changed while it was read')
        directory_after_identity = (
            directory_after.st_dev,
            directory_after.st_ino,
            directory_after.st_uid,
            directory_after.st_mode,
        )
        directory_named_after_identity = (
            directory_named_after.st_dev,
            directory_named_after.st_ino,
            directory_named_after.st_uid,
            directory_named_after.st_mode,
        )
        if directory_identity != directory_after_identity or directory_after_identity != directory_named_after_identity:
            raise SerperConfigError('API key directory changed while the file was read')
        return b''.join(chunks)
    except SerperConfigError:
        raise
    except OSError:
        raise SerperConfigError('API key file changed or could not be read safely') from None
    finally:
        os.close(descriptor)
        os.close(directory_descriptor)


def _parse_config_file(payload):
    try:
        text = payload.decode('ascii')
    except UnicodeDecodeError:
        raise SerperConfigError('API key file must contain ASCII text only') from None
    entries = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('SERPER_API_KEY='):
            value = _strip_optional_quotes(line.split('=', 1)[1].strip())
        elif line.lower().startswith('key:'):
            value = line.split(':', 1)[1].strip()
        elif '=' in line or ':' in line:
            raise SerperConfigError(f'Invalid API key file syntax at line {line_number}')
        else:
            value = line
        if not value:
            raise SerperConfigError(f'Empty API key at line {line_number}')
        entries.append((value, f'config line {line_number}'))
    return _dedupe_and_bound_keys(entries)


def load_api_keys():
    if 'SERPER_API_KEY' in os.environ or 'SERPER_API_KEYS' in os.environ:
        return _parse_environment_keys()
    payload = _read_config_bytes()
    if payload is None:
        return []
    return _parse_config_file(payload)


def get_next_key_index(total_keys):
    if not isinstance(total_keys, int) or total_keys < 1 or total_keys > MAX_API_KEYS:
        return 0
    descriptor = None
    runtime_descriptor = None
    locked = False
    try:
        import fcntl

        RUNTIME_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory_flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        runtime_descriptor = os.open(RUNTIME_DIR, directory_flags)
        runtime_stat = os.fstat(runtime_descriptor)
        if runtime_stat.st_uid not in {os.geteuid(), 0}:
            raise OSError('runtime directory is not owned by the current user')
        os.fchmod(runtime_descriptor, 0o700)
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, 'O_CLOEXEC', 0)
            | getattr(os, 'O_NOFOLLOW', 0)
            | getattr(os, 'O_NONBLOCK', 0)
        )
        descriptor = os.open(RR_INDEX_FILE.name, flags, 0o600, dir_fd=runtime_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError('round-robin state is not a regular file')
        if metadata.st_uid not in {os.geteuid(), 0} or metadata.st_nlink != 1:
            raise OSError('round-robin state has unsafe ownership or hard links')
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw_value = os.read(descriptor, 64).decode('ascii', errors='ignore').strip()
        try:
            saved_index = int(raw_value) if raw_value else 0
        except ValueError:
            saved_index = 0
        current_index = saved_index % total_keys
        next_index = (current_index + 1) % total_keys
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        encoded = str(next_index).encode('ascii')
        written = 0
        while written < len(encoded):
            written += os.write(descriptor, encoded[written:])
        os.fsync(descriptor)
        os.fsync(runtime_descriptor)
        return current_index
    except _RequestDeadlineExpired:
        raise
    except Exception as error:
        _rr_debug(f'fallback to key slot 1 because RR state failed: {type(error).__name__}')
        return 0
    finally:
        if descriptor is not None:
            if locked:
                try:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)
        if runtime_descriptor is not None:
            os.close(runtime_descriptor)


def _validate_identifier(name, value):
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_IDENTIFIER_LENGTH
    ):
        raise SerperAPIError(f'{name} must be non-empty text no longer than {MAX_IDENTIFIER_LENGTH} characters', kind='validation')
    if any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F or char in BIDI_CONTROLS for char in value):
        raise SerperAPIError(f'{name} contains prohibited control characters', kind='validation')
    return value


def _validate_common_request(endpoint, query, num, page, gl, hl):
    if endpoint not in ENDPOINT_URLS:
        raise SerperAPIError('Unsupported Serper endpoint', kind='validation')
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_LENGTH:
        raise SerperAPIError(f'query must be between 1 and {MAX_QUERY_LENGTH} characters', kind='validation')
    if any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F or char in BIDI_CONTROLS for char in query):
        raise SerperAPIError('query contains prohibited control characters', kind='validation')
    if not isinstance(num, int) or isinstance(num, bool) or not 1 <= num <= MAX_NUM:
        raise SerperAPIError(f'num must be between 1 and {MAX_NUM}', kind='validation')
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= MAX_PAGE:
        raise SerperAPIError(f'page must be between 1 and {MAX_PAGE}', kind='validation')
    if not isinstance(gl, str) or not LOCALE_PATTERN.fullmatch(gl):
        raise SerperAPIError('gl is not a valid locale code', kind='validation')
    if not isinstance(hl, str) or not LOCALE_PATTERN.fullmatch(hl):
        raise SerperAPIError('hl is not a valid locale code', kind='validation')


def _build_payload(endpoint, query, num, page, gl, hl, place_id=None, cid=None, fid=None):
    _validate_common_request(endpoint, query, num, page, gl, hl)
    identifiers = {
        'placeId': _validate_identifier('placeId', place_id),
        'cid': _validate_identifier('cid', cid),
        'fid': _validate_identifier('fid', fid),
    }
    supplied = {name: value for name, value in identifiers.items() if value is not None}

    if endpoint == 'reviews':
        if len(supplied) != 1:
            raise SerperAPIError('reviews requires exactly one of placeId, cid, or fid', kind='validation')
        return {**supplied, 'gl': gl, 'hl': hl}
    if supplied:
        raise SerperAPIError('place identifiers are only valid for reviews', kind='validation')
    if endpoint in {'webpage', 'lens'}:
        try:
            validate_public_https_url(query)
        except UsageError as error:
            raise SerperAPIError(str(error), kind='validation') from None
        return {'url': query} if endpoint == 'webpage' else {'url': query, 'gl': gl, 'hl': hl}
    if endpoint == 'maps':
        return {'q': query, 'hl': hl, 'page': page}
    if endpoint == 'autocomplete':
        return {'q': query, 'gl': gl, 'hl': hl}
    return {'q': query, 'num': num, 'page': page, 'gl': gl, 'hl': hl}


def _replace_api_keys(value, api_keys):
    if not isinstance(value, str):
        return value
    for api_key in sorted(api_keys, key=len, reverse=True):
        value = value.replace(api_key, REDACTED_API_KEY)
    return value


def _redact_api_keys(value, api_keys):
    if isinstance(value, str):
        return _replace_api_keys(value, api_keys)
    if isinstance(value, list):
        return [_redact_api_keys(item, api_keys) for item in value]
    if isinstance(value, dict):
        redacted = {}
        for raw_key, raw_value in value.items():
            key = _replace_api_keys(raw_key, api_keys)
            if key in redacted:
                raise SerperAPIError(
                    'Serper returned ambiguous object keys after credential redaction',
                    kind='response',
                )
            redacted[key] = _redact_api_keys(raw_value, api_keys)
        return redacted
    return value


def _contains_api_key(value, api_keys):
    if isinstance(value, str):
        return any(api_key in value for api_key in api_keys)
    if isinstance(value, (list, tuple)):
        return any(_contains_api_key(item, api_keys) for item in value)
    if isinstance(value, dict):
        return any(
            _contains_api_key(key, api_keys) or _contains_api_key(item, api_keys)
            for key, item in value.items()
        )
    return False


def _reject_api_keys_in_payload(payload, api_keys):
    if _contains_api_key(payload, api_keys):
        raise SerperAPIError(
            'Request data must not contain a configured API key',
            kind='validation',
        )


def _decode_json_response(response, api_keys):
    raw_length = response.headers.get('Content-Length') if response.headers else None
    if raw_length:
        try:
            if int(raw_length) > MAX_RESPONSE_BYTES:
                raise SerperAPIError(f'Serper response exceeds the {MAX_RESPONSE_BYTES}-byte limit', kind='response')
        except ValueError:
            pass
    chunks = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise SerperAPIError(f'Serper response exceeds the {MAX_RESPONSE_BYTES}-byte limit', kind='response')
            chunks.append(chunk)
    except SerperAPIError:
        raise
    except _RequestDeadlineExpired:
        raise
    except requests.RequestException:
        raise SerperAPIError('Serper response stream failed', kind='network') from None
    except Exception:
        raise SerperAPIError('Serper response stream could not be read', kind='response') from None
    try:
        decoded = b''.join(chunks).decode('utf-8')
        def bounded_integer(value):
            if _contains_api_key(value, api_keys):
                return REDACTED_API_KEY
            if len(value) > MAX_JSON_NUMBER_CHARS:
                raise ValueError(value[:MAX_JSON_NUMBER_CHARS])
            return int(value)

        def finite_float(value):
            if _contains_api_key(value, api_keys):
                return REDACTED_API_KEY
            if len(value) > MAX_JSON_NUMBER_CHARS:
                raise ValueError(value[:MAX_JSON_NUMBER_CHARS])
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError(value)
            return parsed

        data = json.loads(
            decoded,
            parse_int=bounded_integer,
            parse_float=finite_float,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise SerperAPIError('Serper returned invalid JSON', kind='response') from None
    if not isinstance(data, dict):
        raise SerperAPIError('Serper returned an unexpected top-level response shape', kind='response')
    try:
        redacted = _redact_api_keys(data, api_keys)
    except RecursionError:
        raise SerperAPIError('Serper returned invalid JSON nesting', kind='response') from None
    return sanitize_external_data(redacted)


def _close_response(response, deadline):
    cleanup_timer_armed = False
    close_error = False
    if deadline.expired:
        signal.setitimer(signal.ITIMER_REAL, RESPONSE_CLOSE_TIMEOUT_SECONDS)
        cleanup_timer_armed = True
    try:
        response.close()
    except _RequestDeadlineExpired:
        deadline.expired = True
    except Exception:
        close_error = True
    finally:
        if cleanup_timer_armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
    if deadline.expired:
        raise _RequestDeadlineExpired
    if close_error:
        raise SerperAPIError('Serper response could not be closed safely', kind='network')


def do_request(endpoint, query, num, page=1, gl='cn', hl='zh-cn', place_id=None, cid=None, fid=None):
    deadline = _RequestDeadline(REQUEST_WALL_TIMEOUT_SECONDS)
    try:
        with deadline:
            _validate_common_request(endpoint, query, num, page, gl, hl)
            keys = load_api_keys()
            if not keys:
                raise SerperConfigError('No valid API keys found in the environment or protected config file')
            _reject_api_keys_in_payload(
                {
                    'query': query,
                    'placeId': place_id,
                    'cid': cid,
                    'fid': fid,
                },
                keys,
            )
            payload = _build_payload(
                endpoint,
                query,
                num,
                page,
                gl,
                hl,
                place_id=place_id,
                cid=cid,
                fid=fid,
            )
            _reject_api_keys_in_payload(payload, keys)

            start_index = get_next_key_index(len(keys))
            ordered_indices = list(range(start_index, len(keys))) + list(range(0, start_index))
            retryable_statuses = []
            for key_index in ordered_indices:
                key = keys[key_index]
                key_slot = key_index + 1
                response = None
                try:
                    response = _session.post(
                        ENDPOINT_URLS[endpoint],
                        headers={
                            'X-API-KEY': key,
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                            'User-Agent': USER_AGENT,
                        },
                        json=payload,
                        timeout=REQUEST_TIMEOUT,
                        allow_redirects=False,
                        stream=True,
                    )
                    status_code = response.status_code
                    if status_code == 200:
                        return _decode_json_response(response, keys), key_slot
                    if status_code in FAILOVER_HTTP_STATUSES:
                        retryable_statuses.append(status_code)
                        continue
                    raise SerperAPIError(
                        f'Serper request failed with HTTP {status_code} at keySlot {key_slot}',
                        kind='api',
                    )
                except SerperAPIError:
                    raise
                except requests.Timeout:
                    raise SerperAPIError(
                        f'Serper request timed out at keySlot {key_slot}',
                        kind='network',
                    ) from None
                except requests.RequestException:
                    if deadline.expired:
                        raise _RequestDeadlineExpired from None
                    raise SerperAPIError(
                        f'Serper network request failed at keySlot {key_slot}',
                        kind='network',
                    ) from None
                finally:
                    if response is not None:
                        _close_response(response, deadline)

            status_summary = ','.join(str(status) for status in retryable_statuses)
            raise SerperAPIError(
                f'All configured key slots failed with retryable HTTP statuses: {status_summary}',
                kind='api',
            )
    except _RequestDeadlineExpired:
        raise SerperAPIError(
            f'Serper request exceeded the {REQUEST_WALL_TIMEOUT_SECONDS}-second wall-clock limit',
            kind='network',
        ) from None
