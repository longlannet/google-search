import argparse
import os
import sys

from args import UsageError
from args import parse_args as parse_search_args
from client import SerperAPIError, do_request, load_api_keys
from io_common import (
    safe_print,
    sanitize_external_data,
    sanitize_external_text,
    sanitize_print_text,
)
from renderers_json import serialize_json
from response_shapes import summarize_response_shape
from secure_io import OutputSecurityError, preflight_output_path, secure_write_text
from workflows import run_maps_reviews, run_maps_reviews_all


SELF_CHECK_NOTES = [
    'This is a live health check, not the complete local test suite.',
    'Network state, API quota, and upstream response changes can affect results.',
    'Live checks use us/en rather than the normal cn/zh-cn defaults.',
]
EXIT_CODES = {
    'ok': 0, 'config_error': 2, 'network_error': 3,
    'parsing_error': 4, 'workflow_error': 5, 'mixed_error': 10,
}
ARGUMENT_ERROR_EXIT_CODE = 2
SAFE_ARGUMENT_MESSAGES = frozenset({
    '--group cannot be combined with --basic or --full',
    '--group requires a non-empty value',
    'argument --basic: not allowed with argument --full',
    'argument --full: not allowed with argument --basic',
    'argument --group: expected one argument',
    'argument --save: expected one argument',
    '--save requires a non-empty file path',
    'unknown group value; use --help for supported groups',
})
CHECK_GROUPS = {
    'network-basic': [
        ('search', {'query': 'OpenClaw'}), ('images', {'query': 'cat'}),
        ('news', {'query': 'OpenAI'}), ('autocomplete', {'query': 'openai'}),
        ('maps', {'query': 'coffee shanghai'}),
        ('webpage', {'query': 'https://openclaw.ai'}),
    ],
    'network-full': [
        ('patents', {'query': 'OpenAI'}),
        ('lens', {
            'query': 'https://upload.wikimedia.org/wikipedia/commons/4/47/'
                     'PNG_transparency_demonstration_1.png',
        }),
        ('videos', {'query': 'OpenAI'}), ('places', {'query': 'coffee shanghai'}),
        ('shopping', {'query': 'RTX 5090'}),
        ('scholar', {'query': 'retrieval augmented generation'}),
    ],
    'workflows': [
        ('maps-reviews', {'query': 'coffee shanghai', 'pick': 1}),
        ('maps-reviews-pick2', {'query': 'coffee shanghai', 'pick': 2}),
        ('maps-reviews-all', {'query': 'coffee shanghai'}),
    ],
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
LENS_LIST_KEYS = ('organic', 'visualMatches', 'similarImages', 'images')
NEGATIVE_CHECKS = [
    ('arg-conflict-json-raw', lambda: parse_search_args(['web', 'OpenAI', '--json', '--raw']), UsageError),
    ('reviews-missing-id', lambda: parse_search_args(['reviews']), UsageError),
    ('reviews-multiple-ids', lambda: parse_search_args(['reviews', '--cid', '123', '--fid', '456']), UsageError),
    ('maps-reviews-all-pick-conflict', lambda: parse_search_args(['maps-reviews', 'coffee', '--all', '--pick', '1']), UsageError),
    ('webpage-missing-url', lambda: parse_search_args(['webpage']), UsageError),
    ('num-nonpositive', lambda: parse_search_args(['web', 'OpenAI', '--num', '0']), UsageError),
    ('num-too-large', lambda: parse_search_args(['web', 'OpenAI', '--num', '101']), UsageError),
    ('page-nonpositive', lambda: parse_search_args(['web', 'OpenAI', '--page', '-1']), UsageError),
    ('limit-nonpositive', lambda: parse_search_args(['web', 'OpenAI', '--limit', '0']), UsageError),
    ('unknown-endpoint', lambda: parse_search_args(['newss']), UsageError),
]
GROUP_ALIASES = {
    'network': ['network-basic'], 'network-basic': ['network-basic'],
    'network-full': ['network-full'], 'parsing': ['parsing'], 'workflows': ['workflows'],
    'all-basic': ['network-basic', 'parsing'],
    'all-full': ['network-basic', 'parsing', 'network-full', 'workflows'],
}


class _ArgumentParseError(Exception):
    def __init__(self, message, usage):
        super().__init__(message)
        self.usage = usage


def _safe_argument_error(message):
    """Keep argparse diagnostics useful without reflecting attacker-controlled values."""
    if message.startswith('unrecognized arguments:'):
        return 'unrecognized arguments; use --help for supported options'
    explicit_value_marker = ': ignored explicit argument '
    if explicit_value_marker in message:
        return 'option does not accept a value'
    if message in SAFE_ARGUMENT_MESSAGES:
        return message
    return 'invalid command-line arguments; use --help for supported options'


class _SafeArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            (file or sys.stderr).write(sanitize_print_text(message))

    def error(self, message):
        raise _ArgumentParseError(_safe_argument_error(message), self.format_usage())


def build_selfcheck_parser():
    parser = _SafeArgumentParser(
        prog='selfcheck.py', description='Run grouped google-search health checks.', allow_abbrev=False,
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--basic', action='store_true', help='Run basic network and parsing checks')
    mode_group.add_argument('--full', action='store_true', help='Run every health-check group')
    parser.add_argument('--group', action='append', default=[], help='Group name or comma-separated group names')
    parser.add_argument('--compact', action='store_true', help='Emit compact JSON')
    parser.add_argument('--json', action='store_true', help='Emit JSON (the default output format)')
    parser.add_argument('--fail-fast', action='store_true')
    parser.add_argument('--save', help='Save JSON under an approved output root')
    parser.add_argument('--quiet', '--no-stdout', dest='quiet', action='store_true')
    return parser


def parse_selfcheck_args(argv):
    parser = build_selfcheck_parser()
    namespace = parser.parse_args(argv)
    if namespace.save is not None and not namespace.save:
        parser.error('--save requires a non-empty file path')
    if (namespace.basic or namespace.full) and namespace.group:
        parser.error('--group cannot be combined with --basic or --full')
    if namespace.basic:
        mode = 'basic'
        selected_groups = ['network-basic', 'parsing']
    elif namespace.full:
        mode = 'full'
        selected_groups = ['network-basic', 'parsing', 'network-full', 'workflows']
    elif namespace.group:
        mode = 'group'
        selected_groups = []
        for raw_value in namespace.group:
            pieces = [piece.strip() for piece in raw_value.split(',') if piece.strip()]
            if not pieces:
                parser.error('--group requires a non-empty value')
            for piece in pieces:
                expanded = GROUP_ALIASES.get(piece)
                if expanded is None:
                    parser.error('unknown group value; use --help for supported groups')
                for group_name in expanded:
                    if group_name not in selected_groups:
                        selected_groups.append(group_name)
    else:
        mode = 'basic'
        selected_groups = ['network-basic', 'parsing']
    return {
        'mode': mode,
        'compact': namespace.compact,
        'json_mode': namespace.json or namespace.compact,
        'selected_groups': selected_groups,
        'fail_fast': namespace.fail_fast,
        'save_path': namespace.save,
        'quiet': namespace.quiet,
    }


def _argument_output_intent(argv):
    compact = False
    json_mode = False
    for token in argv:
        if token == '--':
            break
        if token == '--compact' or token.startswith('--compact='):
            compact = True
        elif token == '--json' or token.startswith('--json='):
            json_mode = True
    return json_mode or compact, compact


def emit_argument_error(error, compact=False):
    payload = {
        'ok': False,
        'trust': 'untrusted_external_content',
        'kind': 'selfcheck',
        'errorKind': 'usage_error',
        'error': sanitize_external_text(str(error), max_chars=1_000),
        'exitCode': ARGUMENT_ERROR_EXIT_CODE,
    }
    safe_print(serialize_json(payload, compact=compact))


def emit_human_argument_error(error):
    sys.stderr.write(sanitize_print_text(error.usage))
    sys.stderr.write(f'selfcheck.py: error: {sanitize_print_text(str(error))}\n')


def emit(summary, compact=False, save_path=None, quiet=False):
    safe_summary = sanitize_external_data(summary)
    text = serialize_json(safe_summary, compact=compact)
    if save_path is not None:
        secure_write_text(text, save_path)
    if not quiet:
        safe_print(text)


def emit_output_error(error, compact=False, quiet=False):
    if quiet:
        return
    payload = {
        'ok': False,
        'kind': 'selfcheck',
        'error': sanitize_external_text(str(error), max_chars=1_000),
        'exitCode': EXIT_CODES['mixed_error'],
    }
    safe_print(serialize_json(payload, compact=compact))


def classify_endpoint(endpoint):
    if endpoint in {'maps-reviews', 'maps-reviews-pick2', 'maps-reviews-all'}:
        return 'workflow_error'
    if endpoint in {name for name, _, _ in NEGATIVE_CHECKS}:
        return 'parsing_error'
    return 'network_error'


def note_failure(summary, kind):
    if kind not in summary['failureKinds']:
        summary['failureKinds'].append(kind)


def response_has_list(data, names, minimum=0):
    if not isinstance(data, dict):
        return False
    counts = [len(data[name]) for name in names if isinstance(data.get(name), list)]
    return bool(counts) and max(counts) >= minimum


def selected_place_has_identifier(value):
    if not isinstance(value, dict):
        return False
    return any(
        isinstance(value.get(name), str) and bool(value[name])
        for name in ('placeId', 'cid', 'fid')
    )


def record_result(summary, endpoint, payload, ok=None):
    summary['results'][endpoint] = payload
    effective_ok = payload.get('ok') if isinstance(payload, dict) and 'ok' in payload else ok
    if effective_ok is False:
        summary['ok'] = False
        error_text = payload.get('error', 'failed') if isinstance(payload, dict) else 'failed'
        summary['errors'].append(f'{endpoint}: {sanitize_external_text(error_text)}')
        note_failure(summary, classify_endpoint(endpoint))
    return effective_ok is not False


def run_endpoint_check(summary, endpoint, spec, fail_fast=False):
    summary['endpointsTested'].append(endpoint)
    query = spec.get('query')
    success = True
    try:
        if endpoint == 'maps-reviews-all':
            result = run_maps_reviews_all(query, num=3, page=1, gl='us', hl='en')
            maps_data = result.get('maps')
            places = maps_data.get('places') if isinstance(maps_data, dict) else None
            results = result.get('results')
            review_shapes = [
                summarize_response_shape(entry.get('reviews', {}))
                if isinstance(entry, dict) else summarize_response_shape({})
                for entry in results
            ] if isinstance(results, list) else []
            expected_count = min(len(places), 3) if isinstance(places, list) else 0
            workflow_ok = bool(
                result.get('ok') is True
                and result.get('allSucceeded') is True
                and isinstance(result.get('failedCount'), int)
                and not isinstance(result.get('failedCount'), bool)
                and result.get('failedCount') == 0
                and expected_count > 0
                and isinstance(results, list)
                and len(results) == expected_count
                and all(
                    isinstance(entry, dict)
                    and entry.get('ok') is True
                    and selected_place_has_identifier(entry.get('selectedPlace'))
                    and response_has_list(entry.get('reviews'), ('reviews', 'organic'))
                    for entry in results
                )
            )
            payload = {
                'ok': workflow_ok, 'query': query,
                'resultCount': len(results) if isinstance(results, list) else 0,
                'failedCount': result.get('failedCount', 0),
                'allSucceeded': result.get('allSucceeded', False),
                'mapsShape': summarize_response_shape(maps_data or {}),
                'reviewShapes': review_shapes,
                'error': (
                    result.get('error') or 'maps-reviews-all response failed structural validation'
                ) if not workflow_ok else None,
            }
            success = record_result(summary, endpoint, payload, ok=workflow_ok)
        elif endpoint.startswith('maps-reviews'):
            result = run_maps_reviews(query, num=3, page=1, gl='us', hl='en', pick=spec.get('pick', 1))
            maps_data = result.get('maps')
            reviews_data = result.get('reviews')
            places = maps_data.get('places') if isinstance(maps_data, dict) else None
            expected_pick = spec.get('pick', 1)
            workflow_ok = bool(
                result.get('ok') is True
                and isinstance(places, list)
                and len(places) >= expected_pick
                and result.get('pick') == expected_pick
                and selected_place_has_identifier(result.get('selectedPlace'))
                and response_has_list(reviews_data, ('reviews', 'organic'))
            )
            payload = {
                'ok': workflow_ok, 'query': query, 'pick': expected_pick,
                'selectedPlace': result.get('selectedPlace'),
                'mapsShape': summarize_response_shape(maps_data or {}),
                'reviewsShape': summarize_response_shape(reviews_data or {}),
                'error': (
                    result.get('error') or 'maps-reviews response failed structural validation'
                ) if not workflow_ok else None,
            }
            success = record_result(summary, endpoint, payload, ok=payload['ok'])
        else:
            data, key_slot = do_request(
                endpoint, query, num=3, page=1, gl='us', hl='en',
                place_id=spec.get('place_id'), cid=spec.get('cid'), fid=spec.get('fid'),
            )
            payload = {'ok': True, 'query': query, 'keySlot': key_slot, 'shape': summarize_response_shape(data)}
            if endpoint == 'webpage' and not (
                isinstance(data.get('text'), str) and data['text'].strip()
            ):
                payload.update(ok=False, error='webpage response does not include text')
            elif endpoint == 'lens' and not response_has_list(data, LENS_LIST_KEYS, minimum=1):
                payload.update(ok=False, error='lens response does not include non-empty structured results')
            elif endpoint in NETWORK_LIST_KEYS and not response_has_list(
                data, NETWORK_LIST_KEYS[endpoint], minimum=1,
            ):
                payload.update(
                    ok=False,
                    error=f'{endpoint} response does not include non-empty structured results',
                )
            success = record_result(summary, endpoint, payload, ok=payload['ok'])
    except SerperAPIError as error:
        success = record_result(summary, endpoint, {
            'ok': False, 'query': query, 'error': f'SerperAPIError:{error.kind}: {error}',
        }, ok=False)
    except Exception as error:
        success = record_result(summary, endpoint, {
            'ok': False, 'query': query, 'error': f'Unexpected internal error ({type(error).__name__})',
        }, ok=False)
    if fail_fast and not success:
        raise RuntimeError(f'fail-fast triggered at endpoint: {endpoint}')


def run_negative_checks(summary, fail_fast=False):
    for name, function, expected_error in NEGATIVE_CHECKS:
        summary['endpointsTested'].append(name)
        try:
            function()
            success = record_result(summary, name, {
                'ok': False, 'error': f'Expected {expected_error.__name__} but no exception was raised',
            }, ok=False)
        except expected_error as error:
            success = record_result(summary, name, {
                'ok': True, 'expectedError': expected_error.__name__, 'message': str(error),
            }, ok=True)
        except Exception as error:
            success = record_result(summary, name, {
                'ok': False, 'error': f'Expected {expected_error.__name__}, got {type(error).__name__}',
            }, ok=False)
        if fail_fast and not success:
            raise RuntimeError(f'fail-fast triggered at parsing check: {name}')


def resolve_exit_code(summary):
    if summary.get('ok'):
        return EXIT_CODES['ok']
    failure_kinds = summary.get('failureKinds') or []
    if len(failure_kinds) == 1:
        return EXIT_CODES.get(failure_kinds[0], EXIT_CODES['mixed_error'])
    return EXIT_CODES['mixed_error']


def main(argv=None):
    effective_argv = sys.argv[1:] if argv is None else argv
    try:
        parsed = parse_selfcheck_args(effective_argv)
    except _ArgumentParseError as error:
        machine_mode, compact = _argument_output_intent(effective_argv)
        if machine_mode:
            emit_argument_error(error, compact=compact)
        else:
            emit_human_argument_error(error)
        raise SystemExit(ARGUMENT_ERROR_EXIT_CODE) from None
    try:
        if parsed['save_path'] is not None:
            parsed['save_path'] = os.fspath(preflight_output_path(parsed['save_path']))
    except OutputSecurityError as error:
        emit_output_error(error, parsed['compact'], parsed['quiet'])
        return EXIT_CODES['mixed_error']
    selected_groups = parsed['selected_groups']
    summary = {
        'ok': True, 'trust': 'untrusted_external_content', 'mode': parsed['mode'],
        'kind': 'selfcheck', 'notes': SELF_CHECK_NOTES, 'exitCodes': EXIT_CODES,
        'keyCount': 0, 'selectedGroups': selected_groups,
        'availableGroups': sorted(GROUP_ALIASES), 'failFast': parsed['fail_fast'],
        'savedTo': parsed['save_path'], 'quiet': parsed['quiet'],
        'endpointsTested': [], 'results': {}, 'errors': [], 'failureKinds': [],
    }
    needs_network = any(group != 'parsing' for group in selected_groups)
    if needs_network:
        try:
            keys = load_api_keys()
            summary['keyCount'] = len(keys)
            if not keys:
                raise SerperAPIError('No valid API keys found in the environment or protected config file', kind='config')
        except SerperAPIError as error:
            summary['ok'] = False
            summary['errors'].append(str(error))
            note_failure(summary, 'config_error')
            summary['exitCode'] = resolve_exit_code(summary)
            try:
                emit(summary, parsed['compact'], parsed['save_path'], parsed['quiet'])
            except OutputSecurityError as output_error:
                emit_output_error(output_error, parsed['compact'], parsed['quiet'])
                return EXIT_CODES['mixed_error']
            except OSError:
                emit_output_error(
                    OutputSecurityError('output could not be securely saved'),
                    parsed['compact'], parsed['quiet'],
                )
                return EXIT_CODES['mixed_error']
            return summary['exitCode']

    try:
        for group_name in selected_groups:
            if group_name == 'parsing':
                run_negative_checks(summary, parsed['fail_fast'])
            else:
                for endpoint, spec in CHECK_GROUPS[group_name]:
                    run_endpoint_check(summary, endpoint, spec, parsed['fail_fast'])
    except RuntimeError as error:
        summary['ok'] = False
        summary['errors'].append(str(error))
    summary['exitCode'] = resolve_exit_code(summary)
    try:
        emit(summary, parsed['compact'], parsed['save_path'], parsed['quiet'])
    except OutputSecurityError as error:
        emit_output_error(error, parsed['compact'], parsed['quiet'])
        return EXIT_CODES['mixed_error']
    except OSError:
        emit_output_error(
            OutputSecurityError('output could not be securely saved'),
            parsed['compact'], parsed['quiet'],
        )
        return EXIT_CODES['mixed_error']
    return summary['exitCode']


if __name__ == '__main__':
    raise SystemExit(main())
