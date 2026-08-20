import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import search
import renderers_json
from client import SerperAPIError
from renderers_json import _request_summary


def read_stdout_json(capsys):
    captured = capsys.readouterr()
    assert captured.err == ''
    return json.loads(captured.out)


@pytest.mark.parametrize('flag', ['--json', '--raw'])
def test_parse_errors_honor_machine_output_intent(flag, capsys):
    unsafe_value = 'newss\x1b[31m\u202esecret'
    return_code = search.main([unsafe_value, flag, '--compact'])
    payload = read_stdout_json(capsys)
    assert return_code == 1
    assert payload['ok'] is False
    assert unsafe_value not in payload['error']
    assert '\x1b' not in payload['error'] and '\u202e' not in payload['error']
    assert payload['error'] == 'Unknown mode / endpoint'


@pytest.mark.parametrize('flag', ['--json=unexpected', '--raw=unexpected'])
def test_parse_errors_honor_equals_style_machine_output_intent(flag, capsys):
    assert search.main(['newss', flag, '--compact']) == 1
    payload = read_stdout_json(capsys)
    assert payload['ok'] is False
    assert 'unexpected' not in payload['error']


@pytest.mark.parametrize('argv', [
    ['newss', '--', '--json'],
    ['newss', '--', '--raw'],
    ['newss', '--json-output'],
])
def test_parse_error_intent_ignores_tokens_after_sentinel_and_inexact_flags(argv, capsys):
    assert search.main(argv) == 1
    output = capsys.readouterr().out
    assert output.startswith('Request failed: ')
    with pytest.raises(json.JSONDecodeError):
        json.loads(output)


def test_machine_parse_error_escapes_unpaired_surrogate(capsys):
    assert search.main(['newss\ud800', '--json', '--compact']) == 1
    payload = read_stdout_json(capsys)
    assert r'\ud800' not in payload['error']
    assert payload['error'] == 'Unknown mode / endpoint'


def test_real_cli_parse_error_never_reflects_unknown_secret_or_controls():
    secret = 'sk_test_SUPERSECRET123456'
    hostile = f'--unknown={secret}\x1b\x85\u202e\u2028\u2029'
    completed = subprocess.run(
        [
            sys.executable,
            '-I',
            '-B',
            '-c',
            (
                'import runpy, sys; '
                'script_dir = sys.argv.pop(1); script = sys.argv.pop(1); '
                'sys.path.insert(0, script_dir); runpy.run_path(script, run_name="__main__")'
            ),
            str(SCRIPTS_DIR),
            str(SCRIPTS_DIR / 'search.py'),
            'web',
            'query',
            hostile,
            '--json',
            '--compact',
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 1
    assert completed.stderr == ''
    assert secret not in completed.stdout and hostile not in completed.stdout
    assert '\x1b' not in completed.stdout and '\x85' not in completed.stdout
    assert '\u202e' not in completed.stdout and '\u2028' not in completed.stdout and '\u2029' not in completed.stdout
    assert json.loads(completed.stdout)['error'] == 'Invalid command-line arguments'


def test_pretty_save_is_rejected_before_network(monkeypatch, capsys):
    request = Mock()
    monkeypatch.setattr(search, 'do_request', request)
    assert search.main(['web', 'OpenAI', '--save', '/tmp/result.json']) == 1
    assert '--save requires --json or --raw' in capsys.readouterr().out
    request.assert_not_called()


@pytest.mark.parametrize(('endpoint', 'argv', 'expected_request'), [
    ('search', ['web', 'OpenAI'], {'q': 'OpenAI', 'num': 5, 'page': 1, 'gl': 'cn', 'hl': 'zh-cn'}),
    ('maps', ['maps', 'coffee'], {'q': 'coffee', 'hl': 'zh-cn', 'page': 1}),
    ('autocomplete', ['autocomplete', 'open'], {'q': 'open', 'gl': 'cn', 'hl': 'zh-cn'}),
    ('reviews', ['reviews', '--place-id', 'place-123'], {'placeId': 'place-123', 'gl': 'cn', 'hl': 'zh-cn'}),
    ('webpage', ['webpage', 'https://8.8.8.8/page'], {'url': 'https://8.8.8.8/page'}),
    ('lens', ['lens', 'https://8.8.8.8/image'], {'url': 'https://8.8.8.8/image', 'gl': 'cn', 'hl': 'zh-cn'}),
])
def test_json_wrapper_reports_only_fields_actually_sent(monkeypatch, capsys, endpoint, argv, expected_request):
    monkeypatch.setattr(search, 'do_request', lambda *args, **kwargs: ({'organic': []}, 2))
    assert search.main([*argv, '--json', '--compact']) == 0
    payload = read_stdout_json(capsys)
    assert payload['trust'] == 'untrusted_external_content'
    assert payload['endpoint'] == endpoint
    assert payload['keySlot'] == 2
    assert payload['request'] == expected_request
    assert 'usedKeySuffix' not in payload


def test_request_summary_never_claims_ignored_fields():
    assert _request_summary('webpage', 'https://8.8.8.8/x', 99, 99, 'us', 'en') == {
        'url': 'https://8.8.8.8/x',
    }
    assert 'num' not in _request_summary('reviews', 'ignored', 99, 99, 'us', 'en', cid='cid')
    assert 'gl' not in _request_summary('maps', 'q', 99, 2, 'us', 'en')


@pytest.mark.parametrize(('argv', 'forbidden'), [
    (['webpage', 'https://8.8.8.8/page'], ['page=', 'gl=', 'hl=']),
    (['lens', 'https://8.8.8.8/image'], ['page=', 'num=']),
    (['reviews', '--cid', 'cid-123'], ['page=', 'num=', 'q=']),
])
def test_pretty_banner_omits_unsupported_request_fields(monkeypatch, capsys, argv, forbidden):
    monkeypatch.setattr(search, 'do_request', lambda *args, **kwargs: ({}, 1))
    assert search.main(argv) == 0
    first_line = capsys.readouterr().out.splitlines()[0]
    for field in forbidden:
        assert field not in first_line


def test_logical_workflow_failure_has_nonzero_exit_and_structured_output(monkeypatch, capsys):
    monkeypatch.setattr(search, 'run_maps_reviews', lambda *args, **kwargs: {
        'ok': False, 'query': 'coffee', 'maps': {}, 'reviews': None, 'error': 'No places found',
    })
    assert search.main(['maps-reviews', 'coffee', '--json', '--compact']) == 1
    payload = read_stdout_json(capsys)
    assert payload['ok'] is False
    assert payload['trust'] == 'untrusted_external_content'


def test_all_partial_failure_has_nonzero_exit(monkeypatch, capsys):
    monkeypatch.setattr(search, 'run_maps_reviews_all', lambda *args, **kwargs: {
        'ok': False, 'allSucceeded': False, 'failedCount': 1,
        'query': 'coffee', 'maps': {}, 'results': [], 'error': 'stopped',
    })
    assert search.main(['maps-reviews', 'coffee', '--all', '--json', '--compact']) == 1
    assert read_stdout_json(capsys)['allSucceeded'] is False


def test_workflow_api_exception_uses_unified_error_contract(monkeypatch, capsys):
    monkeypatch.setattr(search, 'run_maps_reviews', Mock(side_effect=SerperAPIError('safe failure')))
    assert search.main(['maps-reviews', 'coffee', '--raw', '--compact']) == 1
    payload = read_stdout_json(capsys)
    assert payload == {'ok': False, 'error': 'safe failure'}


def test_api_error_save_oserror_is_structured_and_does_not_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(search, 'do_request', Mock(side_effect=SerperAPIError('safe failure')))
    monkeypatch.setattr(
        search, 'save_output', Mock(side_effect=OSError('sensitive low-level write detail')),
    )

    assert search.main([
        'web', 'OpenAI', '--json', '--compact', '--save', str(tmp_path / 'result.json'),
    ]) == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload['error'] == 'output could not be securely saved'
    assert 'sensitive' not in captured.out
    assert 'Traceback' not in captured.err


def test_json_save_is_private_and_stdout_matches(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    monkeypatch.setattr(search, 'do_request', lambda *args, **kwargs: ({'organic': []}, 1))
    target = tmp_path / 'output' / 'result.json'
    assert search.main(['web', 'OpenAI', '--json', '--compact', '--save', str(target)]) == 0
    stdout_payload = read_stdout_json(capsys)
    assert json.loads(target.read_text(encoding='utf-8')) == stdout_payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_save_rejects_symlink_without_touching_victim(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    request = Mock(side_effect=AssertionError('save preflight must run before network'))
    monkeypatch.setattr(search, 'do_request', request)
    victim = tmp_path / 'victim'
    victim.write_text('unchanged', encoding='utf-8')
    target = tmp_path / 'result'
    target.symlink_to(victim)
    assert search.main(['web', 'OpenAI', '--json', '--save', str(target)]) == 1
    payload = read_stdout_json(capsys)
    assert payload['ok'] is False
    assert victim.read_text(encoding='utf-8') == 'unchanged'
    request.assert_not_called()


@pytest.mark.parametrize('unsafe_character', ['\x85', '\u2028', '\u202e', '\ud800'])
def test_save_rejects_terminal_unsafe_path_characters(
    monkeypatch, tmp_path, capsys, unsafe_character,
):
    request = Mock(side_effect=AssertionError('save preflight must run before network'))
    monkeypatch.setattr(search, 'do_request', request)
    target = os.fspath(tmp_path) + f'/result{unsafe_character}.json'

    assert search.main(['web', 'OpenAI', '--json', '--compact', '--save', target]) == 1

    payload = read_stdout_json(capsys)
    assert payload['ok'] is False
    assert payload['error'] == 'output path contains unsafe control or directional characters'
    assert list(tmp_path.iterdir()) == []
    request.assert_not_called()


@pytest.mark.parametrize('target', [
    '',
    '/etc/google-search-preflight-result.json',
    '~google-search-user-that-must-not-exist/result.json',
])
def test_save_rejects_empty_or_disallowed_target_before_request(monkeypatch, capsys, target):
    request = Mock(side_effect=AssertionError('save preflight must run before network'))
    monkeypatch.setattr(search, 'do_request', request)

    assert search.main(['web', 'OpenAI', '--json', '--compact', '--save', target]) == 1

    payload = read_stdout_json(capsys)
    assert payload['ok'] is False
    request.assert_not_called()


def test_unexpected_exception_does_not_leak_detail_or_traceback(monkeypatch, capsys):
    monkeypatch.setattr(search, 'do_request', Mock(side_effect=RuntimeError('secret\x1b[31mvalue')))
    assert search.main(['web', 'OpenAI', '--json', '--compact']) == 1
    payload = read_stdout_json(capsys)
    assert 'secret' not in payload['error']
    assert payload['error'] == 'Unexpected internal error (RuntimeError)'


@pytest.mark.parametrize('flag', ['--json', '--raw'])
def test_oversized_machine_output_returns_bounded_structured_error(monkeypatch, capsys, flag):
    monkeypatch.setattr(renderers_json, 'MAX_OUTPUT_BYTES', 512)
    monkeypatch.setattr(search, 'do_request', lambda *args, **kwargs: ({
        'organic': [{'title': 'MUST-NOT-LEAK' + ('x' * 2_000)}],
    }, 1))
    assert search.main(['web', 'OpenAI', flag, '--compact']) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload['ok'] is False
    assert '512-byte limit' in payload['error']
    assert 'MUST-NOT-LEAK' not in captured.out


def test_pretty_output_is_fully_buffered_before_size_check(monkeypatch, capsys):
    monkeypatch.setattr(search, 'MAX_OUTPUT_BYTES', 256)
    monkeypatch.setattr(search, 'do_request', lambda *args, **kwargs: ({
        'organic': [{'title': 'MUST-NOT-LEAK' + ('x' * 2_000), 'link': 'https://example.com'}],
    }, 1))
    assert search.main(['web', 'OpenAI']) == 1
    output = capsys.readouterr().out
    assert output == 'Request failed: pretty output exceeds the 256-byte limit\n'
    assert 'MUST-NOT-LEAK' not in output


def test_maps_reviews_all_pretty_output_uses_same_atomic_size_limit(monkeypatch, capsys):
    monkeypatch.setattr(search, 'MAX_OUTPUT_BYTES', 256)
    monkeypatch.setattr(search, 'run_maps_reviews_all', lambda *args, **kwargs: {
        'ok': True,
        'allSucceeded': True,
        'failedCount': 0,
        'query': 'MUST-NOT-LEAK' + ('x' * 2_000),
        'maps': {},
        'results': [],
    })
    assert search.main(['maps-reviews', 'coffee', '--all']) == 1
    output = capsys.readouterr().out
    assert output == 'Request failed: pretty output exceeds the 256-byte limit\n'
    assert 'MUST-NOT-LEAK' not in output


def test_maps_reviews_all_machine_output_uses_same_atomic_size_limit(monkeypatch, capsys):
    monkeypatch.setattr(renderers_json, 'MAX_OUTPUT_BYTES', 512)
    monkeypatch.setattr(search, 'run_maps_reviews_all', lambda *args, **kwargs: {
        'ok': True,
        'allSucceeded': True,
        'failedCount': 0,
        'query': 'coffee',
        'maps': {'marker': 'MUST-NOT-LEAK' + ('x' * 2_000)},
        'results': [],
    })
    assert search.main(['maps-reviews', 'coffee', '--all', '--json', '--compact']) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload['ok'] is False
    assert '512-byte limit' in payload['error']
    assert 'MUST-NOT-LEAK' not in captured.out
