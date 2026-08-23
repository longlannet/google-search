import json
import fcntl
import os
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import client
import search
import selfcheck
from client import SerperAPIError, SerperConfigError


VALID_KEY_1 = 'serper-test-key-00000001'
VALID_KEY_2 = 'serper-test-key-00000002'


class FakeResponse:
    def __init__(self, status=200, payload=None, body=None, headers=None, chunks=None):
        self.status_code = status
        if body is None:
            body = json.dumps(payload if payload is not None else {}).encode('utf-8')
        self._chunks = chunks if chunks is not None else [body]
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def isolated_key_environment(monkeypatch):
    monkeypatch.delenv('SERPER_API_KEY', raising=False)
    monkeypatch.delenv('SERPER_API_KEYS', raising=False)
    monkeypatch.delenv('SERPER_DEBUG_RR', raising=False)


def make_client(monkeypatch, responses, keys=None, start=0):
    post = Mock(side_effect=responses)
    monkeypatch.setattr(client, '_session', Mock(post=post))
    monkeypatch.setattr(client, 'load_api_keys', lambda: keys or [VALID_KEY_1])
    monkeypatch.setattr(client, 'get_next_key_index', lambda total: start)
    return post


def test_session_does_not_inherit_proxy_netrc_or_ca_environment():
    assert client._session.trust_env is False


@pytest.mark.parametrize(('endpoint', 'query', 'kwargs', 'url', 'payload'), [
    ('search', 'OpenAI', {}, 'https://google.serper.dev/search', {'q': 'OpenAI', 'num': 3, 'page': 2, 'gl': 'us', 'hl': 'en'}),
    ('maps', 'coffee', {}, 'https://google.serper.dev/maps', {'q': 'coffee', 'hl': 'en', 'page': 2}),
    ('autocomplete', 'open', {}, 'https://google.serper.dev/autocomplete', {'q': 'open', 'gl': 'us', 'hl': 'en'}),
    ('reviews', 'ignored', {'place_id': 'place-123'}, 'https://google.serper.dev/reviews', {'placeId': 'place-123', 'gl': 'us', 'hl': 'en'}),
    ('webpage', 'https://8.8.8.8/page', {}, 'https://scrape.serper.dev', {'url': 'https://8.8.8.8/page'}),
    ('lens', 'https://8.8.8.8/image.jpg', {}, 'https://google.serper.dev/lens', {'url': 'https://8.8.8.8/image.jpg', 'gl': 'us', 'hl': 'en'}),
])
def test_endpoint_allowlist_and_official_payloads(monkeypatch, endpoint, query, kwargs, url, payload):
    response = FakeResponse(payload={'organic': []})
    post = make_client(monkeypatch, [response])

    data, slot = client.do_request(endpoint, query, 3, page=2, gl='us', hl='en', **kwargs)

    assert data == {'organic': []}
    assert slot == 1
    call = post.call_args
    assert call.args == (url,)
    assert call.kwargs['json'] == payload
    assert call.kwargs['timeout'] == (5, 15)
    assert call.kwargs['allow_redirects'] is False
    assert call.kwargs['stream'] is True
    assert call.kwargs['headers']['X-API-KEY'] == VALID_KEY_1
    assert response.closed is True


def test_unknown_endpoint_is_rejected_before_config_or_network(monkeypatch):
    load = Mock()
    monkeypatch.setattr(client, 'load_api_keys', load)
    with pytest.raises(SerperAPIError, match='Unsupported'):
        client.do_request('evil', 'query', 3)
    load.assert_not_called()


def test_reviews_requires_exactly_one_identifier():
    with pytest.raises(SerperAPIError, match='exactly one'):
        client._build_payload('reviews', 'q', 3, 1, 'us', 'en')
    with pytest.raises(SerperAPIError, match='exactly one'):
        client._build_payload('reviews', 'q', 3, 1, 'us', 'en', place_id='a', cid='b')


@pytest.mark.parametrize(('endpoint', 'query', 'kwargs'), [
    ('search', '   ', {}),
    ('reviews', 'ignored', {'place_id': '\t'}),
    ('reviews', 'ignored', {'cid': '\N{NO-BREAK SPACE}'}),
])
def test_direct_client_calls_reject_whitespace_only_queries_and_identifiers(
    endpoint, query, kwargs,
):
    with pytest.raises(SerperAPIError, match='non-empty|between 1'):
        client._build_payload(endpoint, query, 3, 1, 'us', 'en', **kwargs)


@pytest.mark.parametrize(('field', 'value'), [
    ('query', 'safe\u202eevil'),
    ('place_id', 'place\u2066evil'),
])
def test_direct_client_calls_reject_bidi_controls(field, value):
    kwargs = {'place_id': 'place-123'}
    query = 'query'
    if field == 'query':
        query = value
    else:
        kwargs[field] = value
    with pytest.raises(SerperAPIError, match='control'):
        client._build_payload('reviews', query, 3, 1, 'us', 'en', **kwargs)


def test_only_auth_quota_statuses_fail_over(monkeypatch):
    first = FakeResponse(status=401, body=b'key one rejected')
    second = FakeResponse(payload={'organic': [{'title': 'ok'}]})
    post = make_client(monkeypatch, [first, second], keys=[VALID_KEY_1, VALID_KEY_2])

    data, slot = client.do_request('search', 'OpenAI', 3)

    assert data['organic'][0]['title'] == 'ok'
    assert slot == 2
    assert post.call_count == 2
    assert first.closed and second.closed


@pytest.mark.parametrize('status', [301, 400, 404, 500, 503])
def test_non_failover_http_status_stops_immediately_without_body_or_key(monkeypatch, status):
    secret = 'serper-secret-visible-tail'
    response = FakeResponse(status=status, body=b'\x1b[31mserver secret body\r\n')
    post = make_client(monkeypatch, [response, FakeResponse()], keys=[secret, VALID_KEY_2])

    with pytest.raises(SerperAPIError) as captured:
        client.do_request('search', 'OpenAI', 3)

    message = str(captured.value)
    assert f'HTTP {status}' in message
    assert 'keySlot 1' in message
    assert secret not in message and secret[-4:] not in message
    assert 'server secret body' not in message and '\x1b' not in message
    assert post.call_count == 1
    assert response.closed


def test_network_error_does_not_fail_over_or_leak_exception(monkeypatch):
    post = make_client(
        monkeypatch,
        [requests.ConnectionError(f'proxy leaked {VALID_KEY_1}'), FakeResponse()],
        keys=[VALID_KEY_1, VALID_KEY_2],
    )
    with pytest.raises(SerperAPIError) as captured:
        client.do_request('search', 'OpenAI', 3)
    assert post.call_count == 1
    assert VALID_KEY_1 not in str(captured.value)


def test_timeout_does_not_fail_over(monkeypatch):
    post = make_client(monkeypatch, [requests.Timeout('slow'), FakeResponse()], keys=[VALID_KEY_1, VALID_KEY_2])
    with pytest.raises(SerperAPIError, match='timed out'):
        client.do_request('search', 'OpenAI', 3)
    assert post.call_count == 1


def test_wall_clock_deadline_interrupts_a_slow_trickle_and_closes_response(monkeypatch):
    class SlowTrickleResponse(FakeResponse):
        def iter_content(self, chunk_size):
            while True:
                time.sleep(0.02)
                yield b'x'

    response = SlowTrickleResponse()
    make_client(monkeypatch, [response])
    monkeypatch.setattr(client, 'REQUEST_WALL_TIMEOUT_SECONDS', 0.08)

    started = time.monotonic()
    with pytest.raises(SerperAPIError, match='wall-clock limit') as captured:
        client.do_request('search', 'OpenAI', 3)
    assert captured.value.kind == 'network'
    assert time.monotonic() - started < 1
    assert response.closed


def test_wall_clock_deadline_is_not_swallowed_by_round_robin_state_io(monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    state = runtime / 'serper_rr.idx'
    post = Mock(side_effect=AssertionError('network must not run after the deadline'))
    monkeypatch.setattr(client, 'RUNTIME_DIR', runtime)
    monkeypatch.setattr(client, 'RR_INDEX_FILE', state)
    monkeypatch.setattr(client, 'load_api_keys', lambda: [VALID_KEY_1])
    monkeypatch.setattr(client, '_session', Mock(post=post))
    monkeypatch.setattr(
        client.os,
        'fsync',
        lambda _descriptor: (_ for _ in ()).throw(client._RequestDeadlineExpired()),
    )

    with pytest.raises(SerperAPIError, match='wall-clock limit') as captured:
        client.do_request('search', 'OpenAI', 3)

    assert captured.value.kind == 'network'
    post.assert_not_called()


def test_wall_clock_deadline_covers_url_dns_before_the_network_request(monkeypatch):
    def slow_dns(*args, **kwargs):
        time.sleep(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]

    monkeypatch.setattr(socket, 'getaddrinfo', slow_dns)
    post = make_client(monkeypatch, [FakeResponse()])
    monkeypatch.setattr(client, 'REQUEST_WALL_TIMEOUT_SECONDS', 0.08)

    started = time.monotonic()
    with pytest.raises(SerperAPIError, match='wall-clock limit'):
        client.do_request('webpage', 'https://example.com/page', 3)
    assert time.monotonic() - started < 1
    post.assert_not_called()


def test_wall_clock_deadline_interrupts_a_blocking_response_close(monkeypatch):
    class SlowCloseResponse(FakeResponse):
        def close(self):
            time.sleep(5)

    response = SlowCloseResponse(payload={'organic': []})
    make_client(monkeypatch, [response])
    monkeypatch.setattr(client, 'REQUEST_WALL_TIMEOUT_SECONDS', 0.08)

    started = time.monotonic()
    with pytest.raises(SerperAPIError, match='wall-clock limit'):
        client.do_request('search', 'OpenAI', 3)
    assert time.monotonic() - started < 1


def test_response_close_exception_is_replaced_with_a_fixed_error(monkeypatch):
    class RaisingCloseResponse(FakeResponse):
        def close(self):
            raise RuntimeError(f'must not leak {VALID_KEY_1}')

    make_client(monkeypatch, [RaisingCloseResponse(payload={'organic': []})])
    with pytest.raises(SerperAPIError, match='could not be closed safely') as captured:
        client.do_request('search', 'OpenAI', 3)
    assert VALID_KEY_1 not in str(captured.value)


def test_wall_clock_deadline_restores_the_process_handler_and_timer(monkeypatch):
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    make_client(monkeypatch, [FakeResponse(payload={'organic': []})])
    monkeypatch.setattr(client, 'REQUEST_WALL_TIMEOUT_SECONDS', 1)

    assert client.do_request('search', 'OpenAI', 3)[0] == {'organic': []}
    assert signal.getsignal(signal.SIGALRM) is previous_handler
    assert signal.getitimer(signal.ITIMER_REAL) == previous_timer


def test_wall_clock_deadline_refuses_to_replace_an_existing_timer(monkeypatch):
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    post = make_client(monkeypatch, [FakeResponse()])
    try:
        signal.setitimer(signal.ITIMER_REAL, 10)
        with pytest.raises(SerperAPIError, match='existing process timer') as captured:
            client.do_request('search', 'OpenAI', 3)
        assert captured.value.kind == 'network'
        post.assert_not_called()
        assert signal.getsignal(signal.SIGALRM) is previous_handler
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


@pytest.mark.parametrize('body', [
    b'not json',
    b'[]',
    b'{"x": NaN}',
    b'{"x": 1e999}',
    b'{"x": ' + (b'1' * 1025) + b'}',
    b'{"x": 0.' + (b'1' * 1025) + b'}',
])
def test_invalid_or_nonfinite_json_stops_without_failover(monkeypatch, body):
    post = make_client(
        monkeypatch,
        [FakeResponse(body=body), FakeResponse()],
        keys=[VALID_KEY_1, VALID_KEY_2],
    )
    with pytest.raises(SerperAPIError):
        client.do_request('search', 'OpenAI', 3)
    assert post.call_count == 1


def test_response_content_length_and_stream_are_bounded(monkeypatch):
    response = FakeResponse(headers={'Content-Length': str(client.MAX_RESPONSE_BYTES + 1)})
    make_client(monkeypatch, [response])
    with pytest.raises(SerperAPIError, match='exceeds'):
        client.do_request('search', 'OpenAI', 3)
    assert response.closed

    chunks = [b'x' * client.MAX_RESPONSE_BYTES, b'x']
    response = FakeResponse(chunks=chunks)
    make_client(monkeypatch, [response])
    with pytest.raises(SerperAPIError, match='exceeds'):
        client.do_request('search', 'OpenAI', 3)
    assert response.closed


def test_external_controls_and_bidi_are_escaped(monkeypatch):
    response = FakeResponse(payload={'organic': [{'title': '\x1b[31mred\nline\u202eevil'}]})
    make_client(monkeypatch, [response])
    data, _ = client.do_request('search', 'OpenAI', 3)
    title = data['organic'][0]['title']
    assert '\x1b' not in title and '\n' not in title and '\u202e' not in title
    assert r'\u001b' in title and r'\u000a' in title and r'\u202e' in title


def test_success_response_redacts_all_configured_keys_before_external_sanitization(monkeypatch):
    response = FakeResponse(payload={
        f'metadata-{VALID_KEY_1}': [
            {'nested': f'before-{VALID_KEY_2}-after'},
            VALID_KEY_1,
        ],
        'organic': [{'title': f'result-{VALID_KEY_1}'}],
    })
    make_client(monkeypatch, [response], keys=[VALID_KEY_1, VALID_KEY_2])
    real_sanitize = client.sanitize_external_data
    observed = {}

    def capture_sanitizer_input(data):
        observed['data'] = data
        return real_sanitize(data)

    monkeypatch.setattr(client, 'sanitize_external_data', capture_sanitizer_input)

    data, slot = client.do_request('search', 'OpenAI', 3)

    assert slot == 1
    serialized = json.dumps(data, sort_keys=True)
    pre_sanitize = json.dumps(observed['data'], sort_keys=True)
    assert VALID_KEY_1 not in serialized and VALID_KEY_2 not in serialized
    assert VALID_KEY_1 not in pre_sanitize and VALID_KEY_2 not in pre_sanitize
    assert client.REDACTED_API_KEY in serialized
    assert f'metadata-{client.REDACTED_API_KEY}' in data
    assert response.closed


def test_response_key_collision_after_api_key_redaction_fails_closed(monkeypatch):
    response = FakeResponse(payload={
        VALID_KEY_1: 'secret-derived key',
        client.REDACTED_API_KEY: 'existing key',
    })
    make_client(monkeypatch, [response])

    with pytest.raises(SerperAPIError, match='ambiguous object keys') as captured:
        client.do_request('search', 'OpenAI', 3)

    assert captured.value.kind == 'response'
    assert VALID_KEY_1 not in str(captured.value)
    assert response.closed


@pytest.mark.parametrize(('api_key', 'body'), [
    ('123456789012', b'{"echo":123456789012}'),
    ('123456789012', b'{"echo":9912345678901299}'),
    ('-123456789012', b'{"echo":-123456789012}'),
    ('1.23456789012e12', b'{"echo":1.23456789012e12}'),
])
def test_json_number_tokens_containing_a_complete_configured_key_are_redacted(
    monkeypatch, api_key, body,
):
    response = FakeResponse(body=body)
    make_client(monkeypatch, [response], keys=[api_key])

    data, _ = client.do_request('search', 'OpenAI', 3)

    serialized = json.dumps(data)
    assert data['echo'] == client.REDACTED_API_KEY
    assert api_key not in serialized
    assert response.closed


@pytest.mark.parametrize(('endpoint', 'query', 'kwargs'), [
    ('search', f'prefix-{VALID_KEY_1}-suffix', {}),
    ('webpage', f'https://{VALID_KEY_1}.example.com/page', {}),
    ('reviews', 'ignored', {'place_id': f'place-{VALID_KEY_1}'}),
    ('reviews', 'ignored', {'cid': f'cid-{VALID_KEY_1}'}),
    ('reviews', 'ignored', {'fid': f'fid-{VALID_KEY_1}'}),
])
def test_request_data_containing_a_configured_key_is_rejected_before_dns_or_http(
    monkeypatch, endpoint, query, kwargs,
):
    post = make_client(monkeypatch, [])
    url_validation = Mock(side_effect=AssertionError('URL validation must not resolve DNS'))
    monkeypatch.setattr(client, 'validate_public_https_url', url_validation)

    with pytest.raises(SerperAPIError, match='must not contain a configured API key') as captured:
        client.do_request(endpoint, query, 3, **kwargs)

    assert captured.value.kind == 'validation'
    assert VALID_KEY_1 not in str(captured.value)
    post.assert_not_called()
    url_validation.assert_not_called()


def test_nested_payload_keys_are_checked_for_configured_api_keys():
    with pytest.raises(SerperAPIError, match='must not contain a configured API key'):
        client._reject_api_keys_in_payload(
            {'outer': [{f'field-{VALID_KEY_1}': 'safe'}]},
            [VALID_KEY_1],
        )


@pytest.mark.parametrize('output_args', [
    [],
    ['--json', '--compact'],
    ['--raw', '--compact'],
])
def test_cli_outputs_never_emit_an_api_key_echoed_by_serper(monkeypatch, capsys, output_args):
    response = FakeResponse(payload={
        'organic': [{
            'title': f'title-{VALID_KEY_1}',
            'link': 'https://example.com/result',
            'snippet': f'snippet-{VALID_KEY_1}',
        }],
        f'metadata-{VALID_KEY_1}': f'value-{VALID_KEY_1}',
    })
    make_client(monkeypatch, [response])
    monkeypatch.setattr(search, 'do_request', client.do_request)

    assert search.main(['web', 'OpenAI', *output_args]) == 0

    captured = capsys.readouterr()
    assert captured.err == ''
    assert VALID_KEY_1 not in captured.out
    assert client.REDACTED_API_KEY in captured.out


def test_saved_output_never_contains_an_api_key_echoed_by_serper(monkeypatch, tmp_path, capsys):
    response = FakeResponse(payload={
        'organic': [{'title': f'title-{VALID_KEY_1}'}],
        f'metadata-{VALID_KEY_1}': f'value-{VALID_KEY_1}',
    })
    make_client(monkeypatch, [response])
    monkeypatch.setattr(search, 'do_request', client.do_request)
    target = tmp_path / 'output' / 'result.json'

    assert search.main([
        'web', 'OpenAI', '--json', '--compact', '--save', str(target),
    ]) == 0

    stdout = capsys.readouterr().out
    saved = target.read_text(encoding='utf-8')
    assert VALID_KEY_1 not in stdout and VALID_KEY_1 not in saved
    assert client.REDACTED_API_KEY in stdout and client.REDACTED_API_KEY in saved


def test_saved_cli_output_redacts_a_numeric_api_key_echoed_as_a_number(
    monkeypatch, tmp_path, capsys,
):
    numeric_key = '123456789012'
    response = FakeResponse(body=b'{"organic":[{"title":"result","position":123456789012}]}')
    make_client(monkeypatch, [response], keys=[numeric_key])
    monkeypatch.setattr(search, 'do_request', client.do_request)
    target = tmp_path / 'output' / 'numeric-key-result.json'

    assert search.main([
        'web', 'OpenAI', '--raw', '--compact', '--save', str(target),
    ]) == 0

    stdout = capsys.readouterr().out
    saved = target.read_text(encoding='utf-8')
    assert numeric_key not in stdout and numeric_key not in saved
    assert client.REDACTED_API_KEY in stdout and client.REDACTED_API_KEY in saved


def test_selfcheck_shape_never_contains_an_api_key_echoed_by_serper(monkeypatch):
    response = FakeResponse(payload={
        'organic': [{'title': 'result'}],
        f'metadata-{VALID_KEY_1}': f'value-{VALID_KEY_1}',
    })
    make_client(monkeypatch, [response])
    monkeypatch.setattr(selfcheck, 'do_request', client.do_request)
    summary = {
        'ok': True,
        'results': {},
        'errors': [],
        'failureKinds': [],
        'endpointsTested': [],
    }

    selfcheck.run_endpoint_check(summary, 'search', {'query': 'OpenAI'})

    serialized = json.dumps(summary, sort_keys=True)
    assert summary['ok'] is True
    assert VALID_KEY_1 not in serialized
    assert client.REDACTED_API_KEY in serialized


def test_environment_presence_takes_priority_over_file(monkeypatch):
    monkeypatch.setenv('SERPER_API_KEY', VALID_KEY_1)
    read_file = Mock(side_effect=AssertionError('file must not be read'))
    monkeypatch.setattr(client, '_read_config_bytes', read_file)
    assert client.load_api_keys() == [VALID_KEY_1]
    read_file.assert_not_called()


def test_environment_multi_key_parsing_is_strict_and_deduplicated(monkeypatch):
    monkeypatch.setenv('SERPER_API_KEY', VALID_KEY_1)
    monkeypatch.setenv('SERPER_API_KEYS', f'{VALID_KEY_2},{VALID_KEY_1}')
    assert client.load_api_keys() == [VALID_KEY_1, VALID_KEY_2]


@pytest.mark.parametrize('value', [
    '', 'your_serper_api_key_here', 'replace_this_with_a_key', 'short',
    'bad key value', 'caf\u00e9-key-000000', 'REDACTED_API_KEY',
])
def test_invalid_environment_keys_are_rejected_without_echo(monkeypatch, value):
    monkeypatch.setenv('SERPER_API_KEY', value)
    with pytest.raises(SerperConfigError) as captured:
        client.load_api_keys()
    assert value not in str(captured.value) or value == ''


def write_config(path, text, mode=0o600):
    path.write_text(text, encoding='utf-8')
    path.chmod(mode)


def test_protected_config_file_parses_supported_formats(monkeypatch, tmp_path):
    config = tmp_path / 'serper.env'
    write_config(config, f'# keys\nSERPER_API_KEY={VALID_KEY_1}\nKey: {VALID_KEY_2}\n{VALID_KEY_1}\n')
    monkeypatch.setattr(client, 'ENV_FILE', config)
    assert client.load_api_keys() == [VALID_KEY_1, VALID_KEY_2]


def test_config_rejects_unknown_assignment_without_echo(monkeypatch, tmp_path):
    config = tmp_path / 'serper.env'
    write_config(config, 'FOO=never-show-this-value\n')
    monkeypatch.setattr(client, 'ENV_FILE', config)
    with pytest.raises(SerperConfigError, match='line 1') as captured:
        client.load_api_keys()
    assert 'never-show' not in str(captured.value)


def test_config_rejects_symlink_and_broad_permissions(monkeypatch, tmp_path):
    real = tmp_path / 'real'
    write_config(real, VALID_KEY_1 + '\n')
    link = tmp_path / 'serper.env'
    link.symlink_to(real)
    monkeypatch.setattr(client, 'ENV_FILE', link)
    with pytest.raises(SerperConfigError, match='safely'):
        client.load_api_keys()

    config = tmp_path / 'broad.env'
    write_config(config, VALID_KEY_1 + '\n', mode=0o640)
    monkeypatch.setattr(client, 'ENV_FILE', config)
    with pytest.raises(SerperConfigError, match='permissions'):
        client.load_api_keys()


def test_config_rejects_symlinked_parent_directory(monkeypatch, tmp_path):
    real_directory = tmp_path / 'real-config'
    real_directory.mkdir(mode=0o700)
    write_config(real_directory / 'serper.env', VALID_KEY_1 + '\n')
    linked_directory = tmp_path / 'linked-config'
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    monkeypatch.setattr(client, 'ENV_FILE', linked_directory / 'serper.env')
    with pytest.raises(SerperConfigError, match='safely'):
        client.load_api_keys()


def test_config_rejects_fifo_without_blocking(tmp_path):
    config = tmp_path / 'serper.env'
    os.mkfifo(config, mode=0o600)
    probe = (
        'import sys\n'
        'from pathlib import Path\n'
        f'sys.path.insert(0, {str(SCRIPTS_DIR)!r})\n'
        'import client\n'
        'client.ENV_FILE = Path(sys.argv[1])\n'
        'try:\n'
        '    client.load_api_keys()\n'
        'except client.SerperConfigError:\n'
        "    print('rejected')\n"
        'else:\n'
        '    raise SystemExit(1)\n'
    )

    completed = subprocess.run(
        [sys.executable, '-B', '-c', probe, str(config)],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert completed.returncode == 0
    assert completed.stdout == 'rejected\n'
    assert completed.stderr == ''


def test_config_rejects_parent_directory_owned_by_another_user(monkeypatch, tmp_path):
    config = tmp_path / 'serper.env'
    write_config(config, VALID_KEY_1 + '\n')
    monkeypatch.setattr(client, 'ENV_FILE', config)
    real_fstat = client.os.fstat

    def untrusted_directory_owner(descriptor):
        metadata = real_fstat(descriptor)
        if stat.S_ISDIR(metadata.st_mode):
            return SimpleNamespace(
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_mode=metadata.st_mode,
                st_uid=123456789,
            )
        return metadata

    monkeypatch.setattr(client.os, 'fstat', untrusted_directory_owner)
    with pytest.raises(SerperConfigError, match='owned'):
        client.load_api_keys()


def test_config_rejects_placeholder_without_echo(monkeypatch, tmp_path):
    config = tmp_path / 'serper.env'
    placeholder = 'your_serper_api_key_here'
    write_config(config, f'SERPER_API_KEY={placeholder}\n')
    monkeypatch.setattr(client, 'ENV_FILE', config)
    with pytest.raises(SerperConfigError) as captured:
        client.load_api_keys()
    assert placeholder not in str(captured.value)


def test_config_rejects_replacement_between_named_stat_and_open(monkeypatch, tmp_path):
    config = tmp_path / 'serper.env'
    replacement = tmp_path / 'replacement.env'
    write_config(config, VALID_KEY_1 + '\n')
    write_config(replacement, VALID_KEY_2 + '\n')
    monkeypatch.setattr(client, 'ENV_FILE', config)
    real_open = client.os.open
    replaced = False

    def replace_before_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if path == config.name and kwargs.get('dir_fd') is not None and not replaced:
            replaced = True
            os.replace(replacement, config)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(client.os, 'open', replace_before_open)

    with pytest.raises(SerperConfigError, match='before it was opened'):
        client.load_api_keys()
    assert replaced


def test_config_rejects_in_place_mutation_while_reading(monkeypatch, tmp_path):
    config = tmp_path / 'serper.env'
    payload = f'SERPER_API_KEY={VALID_KEY_1}\n#' + ('a' * 8_000) + '\n'
    write_config(config, payload)
    monkeypatch.setattr(client, 'ENV_FILE', config)
    real_read = client.os.read
    mutated = False

    def mutate_after_first_read(descriptor, count):
        nonlocal mutated
        chunk = real_read(descriptor, count)
        if chunk and not mutated:
            mutated = True
            with config.open('r+b', buffering=0) as stream:
                stream.seek(0, os.SEEK_END)
                stream.write(b'b')
                os.fsync(stream.fileno())
        return chunk

    monkeypatch.setattr(client.os, 'read', mutate_after_first_read)

    with pytest.raises(SerperConfigError, match='changed while it was read'):
        client.load_api_keys()
    assert mutated


def test_config_rejects_path_replacement_while_reading(monkeypatch, tmp_path):
    config = tmp_path / 'serper.env'
    replacement = tmp_path / 'replacement.env'
    payload = f'SERPER_API_KEY={VALID_KEY_1}\n#' + ('a' * 8_000) + '\n'
    write_config(config, payload)
    write_config(replacement, payload.replace('a', 'b'))
    monkeypatch.setattr(client, 'ENV_FILE', config)
    real_read = client.os.read
    replaced = False

    def replace_after_first_read(descriptor, count):
        nonlocal replaced
        chunk = real_read(descriptor, count)
        if chunk and not replaced:
            replaced = True
            os.replace(replacement, config)
        return chunk

    monkeypatch.setattr(client.os, 'read', replace_after_first_read)

    with pytest.raises(SerperConfigError, match='changed while it was read'):
        client.load_api_keys()
    assert replaced


def test_config_rejects_parent_directory_exchange_while_reading(monkeypatch, tmp_path):
    config_directory = tmp_path / 'config'
    replacement_directory = tmp_path / 'replacement-config'
    displaced_directory = tmp_path / 'displaced-config'
    config_directory.mkdir(mode=0o700)
    replacement_directory.mkdir(mode=0o700)
    config = config_directory / 'serper.env'
    payload = f'SERPER_API_KEY={VALID_KEY_1}\n#' + ('a' * 8_000) + '\n'
    write_config(config, payload)
    write_config(replacement_directory / config.name, payload)
    monkeypatch.setattr(client, 'ENV_FILE', config)
    real_read = client.os.read
    exchanged = False

    def exchange_parent_after_first_read(descriptor, count):
        nonlocal exchanged
        chunk = real_read(descriptor, count)
        if chunk and not exchanged:
            exchanged = True
            config_directory.rename(displaced_directory)
            replacement_directory.rename(config_directory)
        return chunk

    monkeypatch.setattr(client.os, 'read', exchange_parent_after_first_read)

    with pytest.raises(SerperConfigError, match='directory changed'):
        client.load_api_keys()
    assert exchanged


def test_round_robin_state_is_private_and_rotates(monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    state = runtime / 'serper_rr.idx'
    monkeypatch.setattr(client, 'RUNTIME_DIR', runtime)
    monkeypatch.setattr(client, 'RR_INDEX_FILE', state)

    assert client.get_next_key_index(2) == 0
    assert client.get_next_key_index(2) == 1
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert state.read_text(encoding='ascii') == '0'


def test_round_robin_never_chmods_a_path(monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    state = runtime / 'serper_rr.idx'
    monkeypatch.setattr(client, 'RUNTIME_DIR', runtime)
    monkeypatch.setattr(client, 'RR_INDEX_FILE', state)
    monkeypatch.setattr(client.os, 'chmod', lambda *args, **kwargs: pytest.fail('path chmod is unsafe'))
    assert client.get_next_key_index(2) == 0
    assert stat.S_IMODE(state.stat().st_mode) == 0o600


def test_round_robin_refuses_symlink_state(monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir(mode=0o700)
    victim = tmp_path / 'victim'
    victim.write_text('unchanged', encoding='ascii')
    state = runtime / 'serper_rr.idx'
    state.symlink_to(victim)
    monkeypatch.setattr(client, 'RUNTIME_DIR', runtime)
    monkeypatch.setattr(client, 'RR_INDEX_FILE', state)
    assert client.get_next_key_index(2) == 0
    assert victim.read_text(encoding='ascii') == 'unchanged'


def test_round_robin_refuses_fifo_state_without_blocking(monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir(mode=0o700)
    state = runtime / 'serper_rr.idx'
    os.mkfifo(state, mode=0o600)
    monkeypatch.setattr(client, 'RUNTIME_DIR', runtime)
    monkeypatch.setattr(client, 'RR_INDEX_FILE', state)

    assert client.get_next_key_index(2) == 0
    assert stat.S_ISFIFO(state.stat().st_mode)


def test_round_robin_falls_back_immediately_when_state_lock_is_held(monkeypatch, tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir(mode=0o700)
    state = runtime / 'serper_rr.idx'
    state.write_text('1', encoding='ascii')
    state.chmod(0o600)
    monkeypatch.setattr(client, 'RUNTIME_DIR', runtime)
    monkeypatch.setattr(client, 'RR_INDEX_FILE', state)

    descriptor = os.open(state, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        assert client.get_next_key_index(2) == 0
        assert time.monotonic() - started < 1
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
