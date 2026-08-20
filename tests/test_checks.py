import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import selfcheck
import smoke_test
import renderers_json
import check_protocol
from secure_io import OutputSecurityError


def test_parsing_only_selfcheck_does_not_load_keys_or_network(monkeypatch, capsys):
    monkeypatch.setattr(selfcheck, 'load_api_keys', Mock(side_effect=AssertionError('must not read keys')))
    monkeypatch.setattr(selfcheck, 'do_request', Mock(side_effect=AssertionError('must not network')))
    assert selfcheck.main(['--group', 'parsing', '--compact']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['ok'] is True
    assert payload['keyCount'] == 0
    assert payload['selectedGroups'] == ['parsing']
    check_protocol.validate_result(payload, 'parsing')


def test_protocol_network_queries_match_the_real_selfcheck_configuration():
    configured_queries = {
        endpoint: spec['query']
        for group in ('network-basic', 'network-full')
        for endpoint, spec in selfcheck.CHECK_GROUPS[group]
    }
    assert configured_queries == check_protocol.NETWORK_ENDPOINT_QUERIES


@pytest.mark.parametrize('unsafe_character', ['\x85', '\u2028', '\u202e', '\ud800'])
def test_selfcheck_save_rejects_terminal_unsafe_path_characters(
    monkeypatch, tmp_path, capsys, unsafe_character,
):
    load = Mock(side_effect=AssertionError('must not read keys'))
    request = Mock(side_effect=AssertionError('must not network'))
    monkeypatch.setattr(selfcheck, 'load_api_keys', load)
    monkeypatch.setattr(selfcheck, 'do_request', request)
    target = os.fspath(tmp_path) + f'/result{unsafe_character}.json'

    assert selfcheck.main(['--full', '--compact', '--save', target]) == 10

    payload = json.loads(capsys.readouterr().out)
    assert payload['ok'] is False
    assert payload['error'] == 'output path contains unsafe control or directional characters'
    assert list(tmp_path.iterdir()) == []
    load.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize('target', [
    '/etc/google-search-selfcheck-result.json',
    '~google-search-user-that-must-not-exist/result.json',
])
def test_selfcheck_save_rejects_disallowed_path_before_keys_or_network(
    monkeypatch, capsys, target,
):
    load = Mock(side_effect=AssertionError('must not read keys'))
    request = Mock(side_effect=AssertionError('must not network'))
    monkeypatch.setattr(selfcheck, 'load_api_keys', load)
    monkeypatch.setattr(selfcheck, 'do_request', request)

    assert selfcheck.main(['--full', '--compact', '--save', target]) == 10

    payload = json.loads(capsys.readouterr().out)
    assert payload['ok'] is False
    load.assert_not_called()
    request.assert_not_called()


def test_selfcheck_save_oserror_is_structured_and_does_not_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        selfcheck, 'secure_write_text',
        Mock(side_effect=OSError('sensitive low-level write detail')),
    )

    assert selfcheck.main([
        '--group', 'parsing', '--compact', '--save', str(tmp_path / 'result.json'),
    ]) == 10

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload['error'] == 'output could not be securely saved'
    assert 'sensitive' not in captured.out
    assert 'Traceback' not in captured.err


def test_selfcheck_save_rejects_explicit_empty_path_before_keys_or_network(monkeypatch, capsys):
    load = Mock(side_effect=AssertionError('must not read keys'))
    request = Mock(side_effect=AssertionError('must not network'))
    monkeypatch.setattr(selfcheck, 'load_api_keys', load)
    monkeypatch.setattr(selfcheck, 'do_request', request)

    with pytest.raises(SystemExit) as captured:
        selfcheck.main(['--full', '--compact', '--save', ''])

    assert captured.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload['errorKind'] == 'usage_error'
    load.assert_not_called()
    request.assert_not_called()


@pytest.mark.parametrize('argv', [['--ful'], ['--unknown'], ['--group'], ['--basic', '--full']])
def test_selfcheck_unknown_or_invalid_args_exit_before_loading_keys(monkeypatch, argv):
    load = Mock(side_effect=AssertionError('must not read keys'))
    monkeypatch.setattr(selfcheck, 'load_api_keys', load)
    with pytest.raises(SystemExit) as captured:
        selfcheck.main(argv)
    assert captured.value.code == 2
    load.assert_not_called()


@pytest.mark.parametrize(
    'argv',
    [
        ['--group', 'secret-value\x1b[31m\x85\u202etest'],
        ['--unknown=secret-value\x1b[31m\x85\u202etest'],
    ],
)
def test_selfcheck_human_argument_errors_are_terminal_safe_and_do_not_reflect_values(
    monkeypatch, capsys, argv,
):
    secret = 'secret-value'
    load = Mock(side_effect=AssertionError('must not read keys'))
    monkeypatch.setenv('SERPER_API_KEY', secret)
    monkeypatch.setenv('SERPER_API_KEYS', secret)
    monkeypatch.setattr(selfcheck, 'load_api_keys', load)

    with pytest.raises(SystemExit) as captured:
        selfcheck.main(argv)

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert 'usage:' in output.err
    assert 'error:' in output.err
    assert secret not in output.err
    assert '\x1b' not in output.err
    assert '\x85' not in output.err
    assert '\u202e' not in output.err
    load.assert_not_called()


@pytest.mark.parametrize('mode_flag', ['--json', '--compact'])
def test_selfcheck_machine_argument_errors_are_one_safe_json_document(
    monkeypatch, capsys, mode_flag,
):
    secret = 'secret-value'
    malicious = f'--unknown={secret}\x1b[31m\x85\u202etest'
    load = Mock(side_effect=AssertionError('must not read keys'))
    monkeypatch.setenv('SERPER_API_KEY', secret)
    monkeypatch.setenv('SERPER_API_KEYS', secret)
    monkeypatch.setattr(selfcheck, 'load_api_keys', load)

    with pytest.raises(SystemExit) as captured:
        selfcheck.main([mode_flag, malicious])

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.err == ''
    payload = json.loads(output.out)
    assert payload == {
        'ok': False,
        'trust': 'untrusted_external_content',
        'kind': 'selfcheck',
        'errorKind': 'usage_error',
        'error': 'unrecognized arguments; use --help for supported options',
        'exitCode': 2,
    }
    if mode_flag == '--compact':
        assert output.out.count('\n') == 1
    assert secret not in output.out
    assert '\x1b' not in output.out
    assert '\x85' not in output.out
    assert '\u202e' not in output.out
    load.assert_not_called()


@pytest.mark.parametrize('option', ['--json', '--compact'])
def test_selfcheck_machine_flag_values_are_redacted(monkeypatch, capsys, option):
    secret = 'secret-value'
    malicious = f'{option}={secret}\x1b[31m\x85\u202etest'
    load = Mock(side_effect=AssertionError('must not read keys'))
    monkeypatch.setenv('SERPER_API_KEY', secret)
    monkeypatch.setattr(selfcheck, 'load_api_keys', load)

    with pytest.raises(SystemExit) as captured:
        selfcheck.main([malicious])

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.err == ''
    payload = json.loads(output.out)
    assert payload['error'] == 'option does not accept a value'
    assert payload['exitCode'] == 2
    assert secret not in output.out
    assert '\x1b' not in output.out
    assert '\x85' not in output.out
    assert '\u202e' not in output.out
    load.assert_not_called()


def test_selfcheck_help_exits_before_loading_keys(monkeypatch, capsys):
    load = Mock(side_effect=AssertionError('must not read keys'))
    monkeypatch.setattr(selfcheck, 'load_api_keys', load)
    with pytest.raises(SystemExit) as captured:
        selfcheck.main(['--help'])
    assert captured.value.code == 0
    assert 'usage:' in capsys.readouterr().out
    load.assert_not_called()


def test_maps_reviews_all_check_requires_all_succeeded(monkeypatch):
    summary = {
        'ok': True, 'results': {}, 'errors': [], 'failureKinds': [], 'endpointsTested': [],
    }
    monkeypatch.setattr(selfcheck, 'run_maps_reviews_all', lambda *args, **kwargs: {
        'ok': True, 'allSucceeded': False, 'failedCount': 1, 'results': [], 'maps': {},
        'error': 'partial failure',
    })
    selfcheck.run_endpoint_check(summary, 'maps-reviews-all', {'query': 'coffee'})
    assert summary['ok'] is False
    assert summary['results']['maps-reviews-all']['ok'] is False
    assert summary['failureKinds'] == ['workflow_error']


@pytest.mark.parametrize(
    ('endpoint', 'data'),
    [
        *[(name, {keys[0]: []}) for name, keys in selfcheck.NETWORK_LIST_KEYS.items()],
        ('lens', {'visualMatches': []}),
        ('webpage', {'text': ['not', 'text']}),
        ('webpage', {'text': '   '}),
    ],
)
def test_selfcheck_rejects_empty_or_wrong_typed_endpoint_evidence(monkeypatch, endpoint, data):
    summary = {
        'ok': True, 'results': {}, 'errors': [], 'failureKinds': [], 'endpointsTested': [],
    }
    monkeypatch.setattr(selfcheck, 'do_request', lambda *args, **kwargs: (data, 1))

    selfcheck.run_endpoint_check(summary, endpoint, {'query': 'test'})

    assert summary['ok'] is False
    assert summary['results'][endpoint]['ok'] is False
    assert summary['failureKinds'] == ['network_error']


def test_maps_reviews_all_requires_each_review_payload_shape(monkeypatch):
    summary = {
        'ok': True, 'results': {}, 'errors': [], 'failureKinds': [], 'endpointsTested': [],
    }
    monkeypatch.setattr(selfcheck, 'run_maps_reviews_all', lambda *args, **kwargs: {
        'ok': True,
        'allSucceeded': True,
        'failedCount': 0,
        'maps': {'places': [
            {'title': 'A', 'placeId': 'place-a'},
            {'title': 'B', 'placeId': 'place-b'},
        ]},
        'results': [
            {'ok': True, 'selectedPlace': {'placeId': 'place-a'}, 'reviews': {'reviews': []}},
            {'ok': True, 'selectedPlace': {'placeId': 'place-b'}, 'reviews': {}},
        ],
    })

    selfcheck.run_endpoint_check(summary, 'maps-reviews-all', {'query': 'coffee'})

    result = summary['results']['maps-reviews-all']
    assert result['ok'] is False
    assert len(result['reviewShapes']) == 2
    assert summary['failureKinds'] == ['workflow_error']


@pytest.mark.parametrize('argv', [['--unknown'], ['--compact', '--extra']])
def test_smoke_args_are_strict_and_do_not_load_keys(monkeypatch, argv):
    load = Mock(side_effect=AssertionError('must not read keys'))
    monkeypatch.setattr(smoke_test, 'load_api_keys', load)
    with pytest.raises(SystemExit) as captured:
        smoke_test.main(argv)
    assert captured.value.code == 2
    load.assert_not_called()


def test_smoke_human_argument_errors_are_terminal_safe_and_do_not_reflect_values(
    monkeypatch, capsys,
):
    secret = 'secret-value'
    malicious = f'--unknown={secret}\x1b[31m\x85\u202etest'
    load = Mock(side_effect=AssertionError('must not read keys'))
    monkeypatch.setenv('SERPER_API_KEY', secret)
    monkeypatch.setenv('SERPER_API_KEYS', secret)
    monkeypatch.setattr(smoke_test, 'load_api_keys', load)

    with pytest.raises(SystemExit) as captured:
        smoke_test.main([malicious])

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert 'usage:' in output.err
    assert 'error:' in output.err
    assert secret not in output.err
    assert '\x1b' not in output.err
    assert '\x85' not in output.err
    assert '\u202e' not in output.err
    load.assert_not_called()


def test_smoke_compact_argument_errors_are_one_safe_json_document(monkeypatch, capsys):
    secret = 'secret-value'
    malicious = f'--unknown={secret}\x1b[31m\x85\u202etest'
    load = Mock(side_effect=AssertionError('must not read keys'))
    monkeypatch.setenv('SERPER_API_KEY', secret)
    monkeypatch.setenv('SERPER_API_KEYS', secret)
    monkeypatch.setattr(smoke_test, 'load_api_keys', load)

    with pytest.raises(SystemExit) as captured:
        smoke_test.main(['--compact', malicious])

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.err == ''
    assert output.out.count('\n') == 1
    assert json.loads(output.out) == {
        'ok': False,
        'trust': 'untrusted_external_content',
        'kind': 'smoke-test',
        'errorKind': 'usage_error',
        'error': 'unrecognized arguments; use --help for supported options',
        'exitCode': 2,
    }
    assert secret not in output.out
    assert '\x1b' not in output.out
    assert '\x85' not in output.out
    assert '\u202e' not in output.out
    load.assert_not_called()


def test_smoke_compact_flag_values_are_redacted(monkeypatch, capsys):
    secret = 'secret-value'
    malicious = f'--compact={secret}\x1b[31m\x85\u202etest'
    load = Mock(side_effect=AssertionError('must not read keys'))
    monkeypatch.setenv('SERPER_API_KEY', secret)
    monkeypatch.setattr(smoke_test, 'load_api_keys', load)

    with pytest.raises(SystemExit) as captured:
        smoke_test.main([malicious])

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.err == ''
    assert output.out.count('\n') == 1
    payload = json.loads(output.out)
    assert payload['error'] == 'option does not accept a value'
    assert payload['exitCode'] == 2
    assert secret not in output.out
    assert '\x1b' not in output.out
    assert '\x85' not in output.out
    assert '\u202e' not in output.out
    load.assert_not_called()


def test_smoke_help_does_not_load_keys(monkeypatch):
    load = Mock(side_effect=AssertionError('must not read keys'))
    monkeypatch.setattr(smoke_test, 'load_api_keys', load)
    with pytest.raises(SystemExit) as captured:
        smoke_test.main(['--help'])
    assert captured.value.code == 0
    load.assert_not_called()


@pytest.mark.parametrize('emitter', [selfcheck.emit, smoke_test.emit])
def test_health_check_json_emitters_use_shared_output_limit(monkeypatch, emitter, capsys):
    monkeypatch.setattr(renderers_json, 'MAX_OUTPUT_BYTES', 256)
    with pytest.raises(OutputSecurityError, match='256-byte limit'):
        emitter({'value': 'x' * 1_000}, compact=True)
    assert capsys.readouterr().out == ''


def test_smoke_main_turns_output_overflow_into_bounded_json_error(monkeypatch, capsys):
    monkeypatch.setattr(smoke_test, 'load_api_keys', lambda: ['key'])
    monkeypatch.setattr(smoke_test, 'do_request', lambda *args, **kwargs: ({
        'organic': [{'title': 'x'}],
    }, 1))
    real_serialize = smoke_test.serialize_json
    calls = {'count': 0}

    def fail_once(payload, compact=False):
        calls['count'] += 1
        if calls['count'] == 1:
            raise OutputSecurityError('JSON output exceeds the 16777216-byte limit')
        return real_serialize(payload, compact=compact)

    monkeypatch.setattr(smoke_test, 'serialize_json', fail_once)
    assert smoke_test.main(['--compact']) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload['ok'] is False
    assert payload['kind'] == 'smoke-test'


def test_selfcheck_main_turns_output_overflow_into_bounded_json_error(monkeypatch, capsys):
    real_serialize = selfcheck.serialize_json
    calls = {'count': 0}

    def fail_once(payload, compact=False):
        calls['count'] += 1
        if calls['count'] == 1:
            raise OutputSecurityError('JSON output exceeds the 16777216-byte limit')
        return real_serialize(payload, compact=compact)

    monkeypatch.setattr(selfcheck, 'serialize_json', fail_once)
    assert selfcheck.main(['--group', 'parsing', '--compact']) == selfcheck.EXIT_CODES['mixed_error']
    payload = json.loads(capsys.readouterr().out)
    assert payload['ok'] is False
    assert payload['kind'] == 'selfcheck'
    assert payload['exitCode'] == selfcheck.EXIT_CODES['mixed_error']


def _valid_response_shape(list_name=None, count=1, scalar_keys=()):
    top_level_keys = list(scalar_keys)
    list_lengths = {}
    if list_name is not None:
        top_level_keys.append(list_name)
        list_lengths[list_name] = count
    top_level_keys = sorted(set(top_level_keys))
    return {
        'topLevelKeys': top_level_keys,
        'listLengths': list_lengths,
        'hasOrganic': list_lengths.get('organic', 0) > 0,
        'hasAnswerBox': False,
        'hasKnowledgeGraph': False,
        'hasCredits': 'credits' in top_level_keys,
        'hasSearchParameters': False,
        'hasNonEmptyText': 'text' in top_level_keys,
    }


def _valid_endpoint_result(name):
    if name in check_protocol.NEGATIVE_ENDPOINTS:
        return {
            'ok': True,
            'expectedError': 'UsageError',
            'message': check_protocol.NEGATIVE_ENDPOINT_MESSAGES[name],
        }
    if name == 'maps-reviews-all':
        return {
            'ok': True,
            'query': 'coffee shanghai',
            'resultCount': 3,
            'failedCount': 0,
            'allSucceeded': True,
            'mapsShape': _valid_response_shape('places', count=3),
            'reviewShapes': [_valid_response_shape('reviews', count=0) for _ in range(3)],
            'error': None,
        }
    if name in {'maps-reviews', 'maps-reviews-pick2'}:
        return {
            'ok': True,
            'query': 'coffee shanghai',
            'pick': 2 if name == 'maps-reviews-pick2' else 1,
            'selectedPlace': {'placeId': 'test-place'},
            'mapsShape': _valid_response_shape('places', count=3),
            'reviewsShape': _valid_response_shape('reviews', count=1),
            'error': None,
        }
    if name == 'webpage':
        shape = _valid_response_shape(scalar_keys=('text',))
    elif name == 'lens':
        shape = _valid_response_shape('visualMatches')
    else:
        shape = _valid_response_shape(check_protocol.NETWORK_LIST_KEYS[name][0])
    return {
        'ok': True,
        'query': check_protocol.NETWORK_ENDPOINT_QUERIES[name],
        'keySlot': 1,
        'shape': shape,
    }


def _valid_selfcheck_protocol(expected):
    if expected == 'parsing':
        endpoints = check_protocol.NEGATIVE_ENDPOINTS
        mode = 'group'
        groups = ['parsing']
        key_count = 0
    else:
        endpoints = check_protocol.FULL_ENDPOINTS
        mode = 'full'
        groups = check_protocol.FULL_GROUPS
        key_count = 2
    return {
        'ok': True,
        'trust': 'untrusted_external_content',
        'kind': 'selfcheck',
        'mode': mode,
        'exitCode': 0,
        'keyCount': key_count,
        'selectedGroups': groups,
        'endpointsTested': list(endpoints),
        'results': {name: _valid_endpoint_result(name) for name in endpoints},
        'errors': [],
        'failureKinds': [],
    }


def _valid_smoke_protocol():
    return {
        'ok': True,
        'trust': 'untrusted_external_content',
        'kind': 'smoke-test',
        'endpoint': 'search',
        'query': 'OpenClaw',
        'keyCount': 2,
        'keySlot': 2,
        'organicCount': 3,
        'shape': {
            'hasOrganic': True,
            'topLevelKeys': ['organic', 'searchParameters'],
            'listLengths': {'organic': 3},
        },
    }


@pytest.mark.parametrize('expected', ['smoke', 'parsing', 'full'])
@pytest.mark.parametrize('payload', [{}, {'ok': True}, {'ok': True, 'trust': 'untrusted_external_content'}])
def test_check_protocol_rejects_empty_or_partial_success_documents(expected, payload):
    with pytest.raises(ValueError):
        check_protocol.validate_result(payload, expected)


@pytest.mark.parametrize('expected', ['parsing', 'full'])
def test_selfcheck_protocol_requires_exact_endpoint_sentinels(expected):
    payload = _valid_selfcheck_protocol(expected)
    payload['endpointsTested'] = payload['endpointsTested'][:1]
    first_endpoint = payload['endpointsTested'][0]
    payload['results'] = {first_endpoint: _valid_endpoint_result(first_endpoint)}
    with pytest.raises(ValueError, match='endpoint sentinel'):
        check_protocol.validate_result(payload, expected)


@pytest.mark.parametrize('expected', ['parsing', 'full'])
def test_selfcheck_protocol_rejects_forged_all_ok_endpoint_documents(expected):
    payload = _valid_selfcheck_protocol(expected)
    payload['results'] = {name: {'ok': True} for name in payload['endpointsTested']}

    with pytest.raises(ValueError):
        check_protocol.validate_result(payload, expected)


@pytest.mark.parametrize(
    ('endpoint', 'field', 'value'),
    [
        ('arg-conflict-json-raw', 'expectedError', 'ValueError'),
        ('search', 'keySlot', True),
        ('search', 'query', 'different query'),
        ('maps-reviews-pick2', 'pick', 1),
        ('maps-reviews-all', 'resultCount', 0),
        ('maps-reviews-all', 'resultCount', 2),
        ('maps-reviews-all', 'failedCount', 1),
    ],
)
def test_full_selfcheck_protocol_cross_checks_endpoint_semantics(endpoint, field, value):
    payload = _valid_selfcheck_protocol('full')
    payload['results'][endpoint][field] = value

    with pytest.raises(ValueError):
        check_protocol.validate_result(payload, 'full')


def test_parsing_protocol_binds_each_negative_check_to_its_expected_message():
    payload = _valid_selfcheck_protocol('parsing')
    payload['results']['reviews-missing-id']['message'] = check_protocol.NEGATIVE_ENDPOINT_MESSAGES[
        'num-nonpositive'
    ]

    with pytest.raises(ValueError, match='parsing result is incomplete'):
        check_protocol.validate_result(payload, 'parsing')


@pytest.mark.parametrize('endpoint', ['search', 'maps', 'webpage', 'lens', 'maps-reviews'])
def test_full_selfcheck_protocol_requires_endpoint_specific_shapes(endpoint):
    payload = _valid_selfcheck_protocol('full')
    result = payload['results'][endpoint]
    for field in ('shape', 'mapsShape', 'reviewsShape'):
        if field in result:
            result[field] = _valid_response_shape()

    with pytest.raises(ValueError):
        check_protocol.validate_result(payload, 'full')


def test_full_protocol_rejects_empty_or_scalar_endpoint_evidence():
    payload = _valid_selfcheck_protocol('full')
    payload['results']['search']['shape'] = _valid_response_shape('organic', count=0)
    with pytest.raises(ValueError):
        check_protocol.validate_result(payload, 'full')

    payload = _valid_selfcheck_protocol('full')
    payload['results']['maps-reviews-all']['reviewShapes'][1] = _valid_response_shape()
    with pytest.raises(ValueError):
        check_protocol.validate_result(payload, 'full')

    payload = _valid_selfcheck_protocol('full')
    payload['results']['maps-reviews-all']['reviewShapes'].pop()
    with pytest.raises(ValueError):
        check_protocol.validate_result(payload, 'full')

    payload = _valid_selfcheck_protocol('full')
    payload['results']['lens']['shape'] = _valid_response_shape(scalar_keys=('visualMatches',))
    with pytest.raises(ValueError):
        check_protocol.validate_result(payload, 'full')

    payload = _valid_selfcheck_protocol('full')
    payload['results']['webpage']['shape']['hasNonEmptyText'] = False
    with pytest.raises(ValueError):
        check_protocol.validate_result(payload, 'full')


def test_full_protocol_requires_pick2_and_positive_non_boolean_key_count():
    for invalid_key_count in (0, True):
        payload = _valid_selfcheck_protocol('full')
        payload['keyCount'] = invalid_key_count
        with pytest.raises(ValueError, match='mode mismatch'):
            check_protocol.validate_result(payload, 'full')

    payload = _valid_selfcheck_protocol('full')
    payload['endpointsTested'].remove('maps-reviews-pick2')
    payload['results'].pop('maps-reviews-pick2')
    with pytest.raises(ValueError, match='endpoint sentinel'):
        check_protocol.validate_result(payload, 'full')


@pytest.mark.parametrize(('path', 'value'), [
    (('keyCount',), True),
    (('keySlot',), True),
    (('keySlot',), 3),
    (('organicCount',), True),
    (('shape', 'hasOrganic'), False),
    (('shape', 'topLevelKeys'), ['searchParameters']),
    (('shape', 'listLengths', 'organic'), True),
    (('shape', 'listLengths', 'organic'), 2),
])
def test_smoke_protocol_cross_checks_key_and_organic_shape(path, value):
    payload = _valid_smoke_protocol()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        check_protocol.validate_result(payload, 'smoke')


@pytest.mark.parametrize('expected', ['smoke', 'parsing', 'full'])
def test_result_protocol_cli_emits_only_the_expected_sentinel(tmp_path, capsys, expected):
    payload = _valid_smoke_protocol() if expected == 'smoke' else _valid_selfcheck_protocol(expected)
    path = tmp_path / f'{expected}.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    path.chmod(0o600)
    assert check_protocol.main(['result', '--path', str(path), '--expected', expected]) == 0
    assert capsys.readouterr().out == check_protocol.RESULT_SENTINELS[expected] + '\n'


def test_private_result_reader_rejects_empty_and_public_files(tmp_path):
    empty = tmp_path / 'empty.json'
    empty.touch(mode=0o600)
    with pytest.raises(ValueError, match='valid JSON'):
        check_protocol._read_private_json(empty)

    public = tmp_path / 'public.json'
    public.write_text('{}', encoding='utf-8')
    public.chmod(0o644)
    with pytest.raises(ValueError, match='metadata is unsafe'):
        check_protocol._read_private_json(public)


def test_private_result_reader_rejects_fifo_without_blocking(tmp_path):
    fifo = tmp_path / 'result.json'
    os.mkfifo(fifo, mode=0o600)
    result = subprocess.run(
        [
            sys.executable,
            '-I',
            str(SCRIPTS_DIR / 'check_protocol.py'),
            'result',
            '--path',
            str(fifo),
            '--expected',
            'smoke',
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode == 1
    assert result.stdout == ''
    assert result.stderr == ''


def test_private_result_reader_detects_mutation_while_reading(tmp_path, monkeypatch):
    path = tmp_path / 'result.json'
    path.write_text('{"original":true}', encoding='utf-8')
    path.chmod(0o600)
    real_read = check_protocol.os.read
    changed = False

    def mutate_after_read(descriptor, size):
        nonlocal changed
        chunk = real_read(descriptor, size)
        if chunk and not changed:
            changed = True
            path.write_text('{"forged":false}', encoding='utf-8')
        return chunk

    monkeypatch.setattr(check_protocol.os, 'read', mutate_after_read)
    with pytest.raises(ValueError, match='changed while it was read'):
        check_protocol._read_private_json(path)


def _run_real_pytest_protocol(base_dir, required_version='9.1.1'):
    (base_dir / 'requirements-dev.txt').write_text(
        f'pytest=={required_version}\n',
        encoding='utf-8',
    )
    environment = os.environ.copy()
    environment.pop('PYTEST_ADDOPTS', None)
    environment.pop('PYTEST_PLUGINS', None)
    environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    return subprocess.run(
        [
            sys.executable,
            '-I',
            str(SCRIPTS_DIR / 'check_protocol.py'),
            'pytest',
            '--base-dir',
            str(base_dir),
            '--required-version',
            required_version,
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
        timeout=30,
    )


def test_development_lock_version_validation_checks_every_applicable_distribution(monkeypatch):
    expected = check_protocol._expected_development_distributions(BASE_DIR)
    calls = []

    def installed_version(distribution):
        calls.append(distribution)
        return expected[distribution]

    monkeypatch.setattr(check_protocol.importlib.metadata, 'version', installed_version)

    assert check_protocol._verify_development_distribution_versions(
        BASE_DIR,
        '9.1.1',
    ) == expected
    assert calls == sorted(expected)
    expected_names = {
        'certifi',
        'charset-normalizer',
        'idna',
        'iniconfig',
        'packaging',
        'pluggy',
        'pygments',
        'pytest',
        'requests',
        'urllib3',
    }
    if sys.version_info[:2] < (3, 11):
        expected_names.update({'exceptiongroup', 'tomli', 'typing-extensions'})
    if sys.platform == 'win32':
        expected_names.add('colorama')
    assert set(expected) == expected_names


def test_development_lock_version_validation_rejects_transitive_version_mismatch(monkeypatch):
    expected = check_protocol._expected_development_distributions(BASE_DIR)

    def installed_version(distribution):
        return '0.0.0' if distribution == 'pluggy' else expected[distribution]

    monkeypatch.setattr(check_protocol.importlib.metadata, 'version', installed_version)

    with pytest.raises(ValueError, match='version mismatch: pluggy'):
        check_protocol._verify_development_distribution_versions(BASE_DIR, '9.1.1')


def test_real_pytest_protocol_proves_collection_execution_and_sessionfinish(tmp_path):
    tests_dir = tmp_path / 'tests'
    tests_dir.mkdir()
    marker = tmp_path / 'executed'
    (tests_dir / 'test_one.py').write_text(
        f'from pathlib import Path\ndef test_one():\n    Path({str(marker)!r}).write_text("yes")\n',
        encoding='utf-8',
    )
    (tests_dir / 'test_two.py').write_text('def test_two():\n    assert True\n', encoding='utf-8')

    result = _run_real_pytest_protocol(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == check_protocol.PYTEST_SENTINEL + '\n'
    assert marker.read_text(encoding='utf-8') == 'yes'


def test_real_pytest_protocol_does_not_prepend_the_repository_to_imports(tmp_path):
    tests_dir = tmp_path / 'tests'
    tests_dir.mkdir()
    marker = tmp_path / 'hostile-root-module-loaded'
    (tmp_path / 'tarfile.py').write_text(
        f'from pathlib import Path\nPath({str(marker)!r}).write_text("loaded")\n',
        encoding='utf-8',
    )
    (tests_dir / 'test_import.py').write_text(
        'import tarfile\n'
        f'def test_stdlib_import():\n    assert tarfile.__file__ != {str(tmp_path / "tarfile.py")!r}\n',
        encoding='utf-8',
    )

    result = _run_real_pytest_protocol(tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == check_protocol.PYTEST_SENTINEL + '\n'
    assert not marker.exists()


def test_pytest_protocol_rejects_uncollected_file_failure_and_wrong_distribution_version(tmp_path):
    tests_dir = tmp_path / 'tests'
    tests_dir.mkdir()
    (tests_dir / 'test_one.py').write_text('def test_one():\n    assert True\n', encoding='utf-8')
    empty = tests_dir / 'test_empty.py'
    empty.write_text('VALUE = 1\n', encoding='utf-8')
    assert _run_real_pytest_protocol(tmp_path).returncode == 1

    empty.write_text('def test_empty():\n    assert False\n', encoding='utf-8')
    assert _run_real_pytest_protocol(tmp_path).returncode == 1

    empty.write_text('def test_empty():\n    assert True\n', encoding='utf-8')
    assert _run_real_pytest_protocol(tmp_path, required_version='0.0.0').returncode == 1


def test_online_key_protocol_uses_only_explicit_fake_client(tmp_path):
    scripts_dir = tmp_path / 'scripts'
    scripts_dir.mkdir()
    client = scripts_dir / 'client.py'
    client.write_text(
        'class SerperConfigError(Exception):\n    pass\n'
        'def load_api_keys():\n    return ["test-only-key"]\n',
        encoding='utf-8',
    )
    check_protocol.validate_online_keys(client)

    client.write_text(
        'class SerperConfigError(Exception):\n    pass\n'
        'def load_api_keys():\n    return []\n',
        encoding='utf-8',
    )
    with pytest.raises(check_protocol.OnlineConfigError):
        check_protocol.validate_online_keys(client)


def test_online_key_protocol_distinguishes_config_and_unexpected_failures(tmp_path):
    client = tmp_path / 'client.py'
    client.write_text(
        'class SerperConfigError(Exception):\n    pass\n'
        'def load_api_keys():\n    raise SerperConfigError("bad config")\n',
        encoding='utf-8',
    )
    with pytest.raises(check_protocol.OnlineConfigError):
        check_protocol.validate_online_keys(client)

    client.write_text(
        'class SerperConfigError(Exception):\n    pass\n'
        'def load_api_keys():\n    raise RuntimeError("helper bug")\n',
        encoding='utf-8',
    )
    with pytest.raises(ValueError, match='unexpectedly'):
        check_protocol.validate_online_keys(client)


def test_online_key_protocol_treats_path_loader_and_api_faults_as_internal_errors(tmp_path, monkeypatch):
    wrong_name = tmp_path / 'not-client.py'
    wrong_name.write_text('raise AssertionError("must not import")\n', encoding='utf-8')
    with pytest.raises(ValueError, match='path is unsafe') as wrong_name_error:
        check_protocol.validate_online_keys(wrong_name)
    assert type(wrong_name_error.value) is ValueError

    client = tmp_path / 'client.py'
    client.write_text(
        'class SerperConfigError(Exception):\n    pass\n'
        'def load_api_keys():\n    return ["test-only-key"]\n',
        encoding='utf-8',
    )
    symlink = tmp_path / 'linked' / 'client.py'
    symlink.parent.mkdir()
    symlink.symlink_to(client)
    with pytest.raises(ValueError, match='path is unsafe') as symlink_error:
        check_protocol.validate_online_keys(symlink)
    assert type(symlink_error.value) is ValueError

    monkeypatch.setattr(check_protocol.importlib.util, 'spec_from_file_location', lambda *args: None)
    with pytest.raises(ValueError, match='cannot be loaded') as loader_error:
        check_protocol.validate_online_keys(client)
    assert type(loader_error.value) is ValueError


@pytest.mark.parametrize(
    'source',
    [
        'def load_api_keys():\n    return ["test-only-key"]\n',
        'class SerperConfigError(Exception):\n    pass\n',
        (
            'class SerperConfigError(Exception):\n    pass\n'
            'def load_api_keys():\n    return None\n'
        ),
        (
            'class SerperConfigError(Exception):\n    pass\n'
            'def load_api_keys():\n    return [""]\n'
        ),
    ],
)
def test_online_key_protocol_treats_incomplete_or_malformed_api_as_internal_error(tmp_path, source):
    client = tmp_path / 'client.py'
    client.write_text(source, encoding='utf-8')

    with pytest.raises(ValueError) as error:
        check_protocol.validate_online_keys(client)

    assert type(error.value) is ValueError


def test_pytest_protocol_trust_walk_is_recursive_and_rejects_unsafe_entries(tmp_path):
    tests_dir = tmp_path / 'tests'
    nested = tests_dir / 'nested'
    nested.mkdir(parents=True)
    nested_test = nested / 'test_nested.py'
    nested_test.write_text('def test_nested():\n    assert True\n', encoding='utf-8')

    _, _, expected = check_protocol._expected_test_files(tmp_path)
    assert expected == {nested_test.resolve()}

    nested_test.chmod(0o666)
    with pytest.raises(ValueError, match='unsafe ownership or permissions'):
        check_protocol._expected_test_files(tmp_path)
    nested_test.chmod(0o644)

    victim = tmp_path / 'victim.py'
    victim.write_text('def test_victim():\n    assert True\n', encoding='utf-8')
    link = tests_dir / 'test_link.py'
    link.symlink_to(victim)
    with pytest.raises(ValueError, match='symlink or special file'):
        check_protocol._expected_test_files(tmp_path)


def _release_source_fixture(tmp_path, *, relative_name='tracked.py', payload=b'VALUE = 1\n'):
    root = tmp_path / 'source'
    root.mkdir(mode=0o700, parents=True)
    source = root / relative_name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)
    source.chmod(0o644)
    digest = check_protocol.hashlib.sha1(
        f'blob {len(payload)}\0'.encode('ascii'),
        usedforsecurity=False,
    )
    digest.update(payload)
    object_id = digest.hexdigest().encode('ascii')
    encoded_name = relative_name.encode('utf-8')
    head = tmp_path / 'head-tree.z'
    index = tmp_path / 'index-stage.z'
    head.write_bytes(b'100644 blob ' + object_id + b'\t' + encoded_name + b'\0')
    index.write_bytes(b'100644 ' + object_id + b' 0\t' + encoded_name + b'\0')
    head.chmod(0o600)
    index.chmod(0o600)
    return root, source, head, index


def test_release_source_protocol_binds_worktree_index_and_head(tmp_path, capsys):
    root, source, head, index = _release_source_fixture(tmp_path)

    check_protocol.validate_release_source(root, head, index)
    assert check_protocol.main([
        'release-source', '--base-dir', str(root), '--', str(head), str(index),
    ]) == 0
    assert capsys.readouterr().out == check_protocol.RELEASE_SOURCE_SENTINEL + '\n'

    source.write_bytes(b'VALUE = 2\n')
    with pytest.raises(ValueError, match='committed blob'):
        check_protocol.validate_release_source(root, head, index)


def test_release_source_protocol_rejects_index_or_manifest_metadata_forgery(tmp_path):
    root, _, head, index = _release_source_fixture(tmp_path)
    index.write_bytes(index.read_bytes().replace(b' 0\t', b' 1\t'))
    with pytest.raises(ValueError, match='unsafe entry'):
        check_protocol.validate_release_source(root, head, index)

    index.write_bytes(head.read_bytes().replace(b' blob ', b' ').replace(b'\t', b' 0\t'))
    index.chmod(0o644)
    with pytest.raises(ValueError, match='metadata is unsafe'):
        check_protocol.validate_release_source(root, head, index)


def test_release_source_protocol_rejects_symlinked_parent_and_unsafe_names(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'tracked.py').write_text('VALUE = 1\n', encoding='utf-8')
    root, _, head, index = _release_source_fixture(tmp_path / 'fixture')
    (root / 'nested').symlink_to(outside, target_is_directory=True)
    head_payload = head.read_bytes().replace(b'tracked.py\0', b'nested/tracked.py\0')
    index_payload = index.read_bytes().replace(b'tracked.py\0', b'nested/tracked.py\0')
    head.write_bytes(head_payload)
    index.write_bytes(index_payload)

    with pytest.raises(ValueError, match='parent metadata is unsafe'):
        check_protocol.validate_release_source(root, head, index)

    head.write_bytes(head_payload.replace(b'nested/tracked.py', b'../tracked.py'))
    index.write_bytes(index_payload.replace(b'nested/tracked.py', b'../tracked.py'))
    with pytest.raises(ValueError, match='unsafe path'):
        check_protocol.validate_release_source(root, head, index)
