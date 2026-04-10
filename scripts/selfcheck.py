#!/usr/bin/env python3
import json
import sys
from pathlib import Path

from args import UsageError
from args import parse_args as parse_search_args
from client import SerperAPIError, do_request, load_api_keys
from io_common import safe_print
from response_shapes import summarize_response_shape
from workflows import run_maps_reviews, run_maps_reviews_all


SELF_CHECK_NOTES = [
    '这是联网健康检查，不是完整单元测试。',
    '结果会受到网络状态、API 配额、Serper 返回结构变化等因素影响。',
    '自检固定使用 us/en，以尽量降低区域差异对公开测试结果的影响；这与程序默认的 cn/zh-cn 不同。',
]

EXIT_CODES = {
    'ok': 0,
    'config_error': 2,
    'network_error': 3,
    'parsing_error': 4,
    'workflow_error': 5,
    'mixed_error': 10,
}

CHECK_GROUPS = {
    'network-basic': [
        ('search', {'query': 'OpenClaw'}),
        ('images', {'query': 'cat'}),
        ('news', {'query': 'OpenAI'}),
        ('autocomplete', {'query': 'openai'}),
        ('maps', {'query': 'coffee shanghai'}),
        ('webpage', {'query': 'https://openclaw.ai'}),
    ],
    'network-full': [
        ('patents', {'query': 'OpenAI'}),
        ('lens', {'query': 'https://openclaw.ai/favicon.ico'}),
        ('videos', {'query': 'OpenAI'}),
        ('places', {'query': 'coffee shanghai'}),
        ('shopping', {'query': 'RTX 5090'}),
        ('scholar', {'query': 'retrieval augmented generation'}),
    ],
    'workflows': [
        ('maps-reviews', {'query': 'coffee shanghai', 'pick': 1}),
        ('maps-reviews-pick2', {'query': 'coffee shanghai', 'pick': 2}),
        ('maps-reviews-all', {'query': 'coffee shanghai'}),
    ],
}

NEGATIVE_CHECKS = [
    ('arg-conflict-json-raw', lambda: parse_search_args(['web', 'OpenAI', '--json', '--raw']), UsageError),
    ('reviews-missing-id', lambda: parse_search_args(['reviews']), UsageError),
    ('maps-reviews-all-pick-conflict', lambda: parse_search_args(['maps-reviews', 'coffee shanghai', '--all', '--pick', '2']), UsageError),
    ('webpage-missing-url', lambda: parse_search_args(['webpage']), UsageError),
    ('num-nonpositive', lambda: parse_search_args(['web', 'OpenAI', '--num', '0']), UsageError),
    ('page-nonpositive', lambda: parse_search_args(['web', 'OpenAI', '--page', '-1']), UsageError),
    ('limit-nonpositive', lambda: parse_search_args(['web', 'OpenAI', '--limit', '0']), UsageError),
    ('unknown-endpoint', lambda: parse_search_args(['newss']), UsageError),
]

GROUP_ALIASES = {
    'network': ['network-basic'],
    'network-basic': ['network-basic'],
    'network-full': ['network-full'],
    'parsing': ['parsing'],
    'workflows': ['workflows'],
    'all-basic': ['network-basic', 'parsing'],
    'all-full': ['network-basic', 'parsing', 'network-full', 'workflows'],
}


def parse_selfcheck_args(argv):
    compact = '--compact' in argv
    json_mode = '--json' in argv or compact
    fail_fast = '--fail-fast' in argv
    quiet = '--quiet' in argv or '--no-stdout' in argv

    basic = '--basic' in argv
    full = '--full' in argv
    if basic and full:
        raise SystemExit('selfcheck: --basic cannot be combined with --full')

    group_values = []
    save_path = None
    idx = 0
    while idx < len(argv):
        token = argv[idx]
        if token == '--group':
            if idx + 1 >= len(argv):
                raise SystemExit('selfcheck: --group requires a value')
            group_values.append(argv[idx + 1])
            idx += 2
            continue
        if token == '--save':
            if idx + 1 >= len(argv):
                raise SystemExit('selfcheck: --save requires a file path')
            save_path = argv[idx + 1]
            idx += 2
            continue
        idx += 1

    if (basic or full) and group_values:
        raise SystemExit('selfcheck: --group cannot be combined with --basic or --full')

    if basic:
        mode = 'basic'
        selected_groups = ['network-basic', 'parsing']
    elif full:
        mode = 'full'
        selected_groups = ['network-basic', 'parsing', 'network-full', 'workflows']
    elif group_values:
        mode = 'group'
        selected_groups = []
        for raw in group_values:
            pieces = [item.strip() for item in raw.split(',') if item.strip()]
            if not pieces:
                raise SystemExit('selfcheck: --group requires a non-empty value')
            for piece in pieces:
                expanded = GROUP_ALIASES.get(piece)
                if not expanded:
                    raise SystemExit(f'selfcheck: unknown group: {piece}')
                for name in expanded:
                    if name not in selected_groups:
                        selected_groups.append(name)
    else:
        mode = 'basic'
        selected_groups = ['network-basic', 'parsing']

    return {
        'mode': mode,
        'compact': compact,
        'json_mode': json_mode,
        'selected_groups': selected_groups,
        'fail_fast': fail_fast,
        'save_path': save_path,
        'quiet': quiet,
    }


def emit(summary, compact=False, save_path=None, quiet=False):
    text = json.dumps(summary, ensure_ascii=False, separators=(',', ':')) if compact else json.dumps(summary, ensure_ascii=False, indent=2)
    if save_path:
        path = Path(save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + '\n', encoding='utf-8')
    if not quiet:
        safe_print(text)


def classify_endpoint(endpoint):
    if endpoint in {'maps-reviews', 'maps-reviews-pick2', 'maps-reviews-all'}:
        return 'workflow_error'
    if endpoint in {name for name, _, _ in NEGATIVE_CHECKS}:
        return 'parsing_error'
    return 'network_error'


def note_failure(summary, kind):
    failures = summary.setdefault('failureKinds', [])
    if kind not in failures:
        failures.append(kind)


def record_result(summary, endpoint, payload, ok=None):
    summary['results'][endpoint] = payload
    effective_ok = payload.get('ok') if isinstance(payload, dict) and 'ok' in payload else ok
    if effective_ok is False:
        summary['ok'] = False
        error_text = payload.get('error', 'failed') if isinstance(payload, dict) else 'failed'
        summary['errors'].append(f'{endpoint}: {error_text}')
        note_failure(summary, classify_endpoint(endpoint))
    return effective_ok is not False


def run_endpoint_check(summary, endpoint, spec, fail_fast=False):
    summary['endpointsTested'].append(endpoint)
    query = spec.get('query')
    success = True
    try:
        if endpoint == 'maps-reviews-all':
            result = run_maps_reviews_all(query, num=3, page=1, gl='us', hl='en')
            payload = {
                'ok': result.get('ok', False),
                'query': query,
                'resultCount': len(result.get('results', [])),
                'failedCount': result.get('failedCount', 0),
                'allSucceeded': result.get('allSucceeded', False),
                'mapsShape': summarize_response_shape(result.get('maps', {})),
            }
            success = record_result(summary, endpoint, payload, ok=result.get('ok', False))
        elif endpoint.startswith('maps-reviews'):
            result = run_maps_reviews(query, num=3, page=1, gl='us', hl='en', pick=spec.get('pick', 1))
            payload = {
                'ok': result.get('ok', False),
                'query': query,
                'pick': spec.get('pick', 1),
                'selectedPlace': result.get('selectedPlace'),
                'mapsShape': summarize_response_shape(result.get('maps', {})),
                'reviewsShape': summarize_response_shape(result.get('reviews', {})),
            }
            success = record_result(summary, endpoint, payload, ok=result.get('ok', False))
        else:
            data, used_key = do_request(
                endpoint,
                query,
                num=3,
                page=1,
                gl='us',
                hl='en',
                place_id=spec.get('place_id'),
                cid=spec.get('cid'),
                fid=spec.get('fid'),
            )
            payload = {
                'ok': True,
                'query': query,
                'usedKeySuffix': used_key[-4:],
                'shape': summarize_response_shape(data),
            }

            if endpoint == 'maps':
                payload['hasPlaces'] = bool(data.get('places'))
                if not payload['hasPlaces']:
                    payload['ok'] = False
                    payload['error'] = 'maps response does not include places'
            elif endpoint == 'webpage':
                payload['hasText'] = bool((data.get('text') or '').strip())
                if not payload['hasText']:
                    payload['ok'] = False
                    payload['error'] = 'webpage response does not include text'
            elif endpoint == 'lens':
                payload['hasStructuredResult'] = any(
                    key in data for key in ['organic', 'visualMatches', 'similarImages', 'images']
                )
                if not payload['hasStructuredResult']:
                    payload['ok'] = False
                    payload['error'] = 'lens response does not include structured result fields'

            success = record_result(summary, endpoint, payload, ok=payload.get('ok', False))
    except (SystemExit, SerperAPIError) as e:
        success = record_result(summary, endpoint, {
            'ok': False,
            'query': query,
            'error': f'API Error / Exit: {e}',
        }, ok=False)
    except Exception as e:
        success = record_result(summary, endpoint, {
            'ok': False,
            'query': query,
            'error': f'{type(e).__name__}: {e}',
        }, ok=False)

    if fail_fast and not success:
        raise RuntimeError(f'fail-fast triggered at endpoint: {endpoint}')



def run_negative_checks(summary, fail_fast=False):
    for name, fn, expected_exc in NEGATIVE_CHECKS:
        summary['endpointsTested'].append(name)
        success = True
        try:
            fn()
            success = record_result(summary, name, {
                'ok': False,
                'error': f'Expected {expected_exc.__name__} but no exception was raised',
            }, ok=False)
        except expected_exc as e:
            success = record_result(summary, name, {
                'ok': True,
                'expectedError': expected_exc.__name__,
                'message': str(e),
            }, ok=True)
        except Exception as e:
            success = record_result(summary, name, {
                'ok': False,
                'error': f'Expected {expected_exc.__name__}, got {type(e).__name__}: {e}',
            }, ok=False)

        if fail_fast and not success:
            raise RuntimeError(f'fail-fast triggered at parsing check: {name}')


def resolve_exit_code(summary):
    if summary.get('ok'):
        return EXIT_CODES['ok']
    failure_kinds = summary.get('failureKinds') or []
    if not failure_kinds:
        return EXIT_CODES['mixed_error']
    if len(failure_kinds) == 1:
        return EXIT_CODES.get(failure_kinds[0], EXIT_CODES['mixed_error'])
    return EXIT_CODES['mixed_error']


def main():
    args = parse_selfcheck_args(sys.argv[1:])
    mode = args['mode']
    compact = args['compact']
    selected_groups = args['selected_groups']
    fail_fast = args['fail_fast']
    save_path = args['save_path']
    quiet = args['quiet']

    keys = load_api_keys()
    summary = {
        'ok': True,
        'mode': mode,
        'kind': 'selfcheck',
        'notes': SELF_CHECK_NOTES,
        'exitCodes': EXIT_CODES,
        'keyCount': len(keys),
        'selectedGroups': selected_groups,
        'availableGroups': sorted(GROUP_ALIASES.keys()),
        'failFast': fail_fast,
        'savedTo': save_path,
        'quiet': quiet,
        'endpointsTested': [],
        'results': {},
        'errors': [],
        'failureKinds': [],
    }

    if not keys:
        summary['ok'] = False
        summary['errors'].append('No valid API keys found in config/serper.env or SERPER_API_KEY')
        note_failure(summary, 'config_error')
        summary['exitCode'] = resolve_exit_code(summary)
        emit(summary, compact=compact, save_path=save_path, quiet=quiet)
        sys.exit(summary['exitCode'])

    try:
        if 'network-basic' in selected_groups:
            for endpoint, spec in CHECK_GROUPS['network-basic']:
                run_endpoint_check(summary, endpoint, spec, fail_fast=fail_fast)

        if 'parsing' in selected_groups:
            run_negative_checks(summary, fail_fast=fail_fast)

        if 'network-full' in selected_groups:
            for endpoint, spec in CHECK_GROUPS['network-full']:
                run_endpoint_check(summary, endpoint, spec, fail_fast=fail_fast)

        if 'workflows' in selected_groups:
            for endpoint, spec in CHECK_GROUPS['workflows']:
                run_endpoint_check(summary, endpoint, spec, fail_fast=fail_fast)
    except RuntimeError as e:
        summary['ok'] = False
        summary['errors'].append(str(e))

    summary['exitCode'] = resolve_exit_code(summary)
    emit(summary, compact=compact, save_path=save_path, quiet=quiet)
    sys.exit(summary['exitCode'])


if __name__ == '__main__':
    main()
