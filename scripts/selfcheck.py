#!/usr/bin/env python3
import json
import sys

from args import parse_args as parse_search_args
from args import UsageError
from utils import SerperAPIError, do_request, load_api_keys, run_maps_reviews, run_maps_reviews_all, safe_print, summarize_response_shape


SELF_CHECK_NOTES = [
    '这是联网健康检查（smoke test），不是完整单元测试。',
    '结果会受到网络状态、API 配额、Serper 返回结构变化等因素影响。',
]


def parse_args(argv):
    full = '--full' in argv
    compact = '--compact' in argv
    return {
        'full': full,
        'compact': compact,
    }


def emit(summary, compact=False):
    if compact:
        safe_print(json.dumps(summary, ensure_ascii=False, separators=(',', ':')))
    else:
        safe_print(json.dumps(summary, ensure_ascii=False, indent=2))


def record_result(summary, endpoint, payload, ok=None):
    summary['results'][endpoint] = payload
    if ok is False:
        summary['ok'] = False
        error_text = payload.get('error', 'failed')
        summary['errors'].append(f'{endpoint}: {error_text}')


def main():
    args = parse_args(sys.argv[1:])
    full = args['full']
    compact = args['compact']

    keys = load_api_keys()
    summary = {
        'ok': True,
        'mode': 'full' if full else 'basic',
        'kind': 'smoke-test',
        'notes': SELF_CHECK_NOTES,
        'keyCount': len(keys),
        'endpointsTested': [],
        'results': {},
        'errors': [],
    }

    if not keys:
        summary['ok'] = False
        summary['errors'].append('No valid API keys found in config/serper.env or SERPER_API_KEY')
        emit(summary, compact=compact)
        sys.exit(1)

    checks = [
        ('search', {'query': 'OpenClaw'}),
        ('images', {'query': 'cat'}),
        ('news', {'query': 'OpenAI'}),
        ('autocomplete', {'query': 'openai'}),
        ('maps', {'query': 'coffee shanghai'}),
        ('patents', {'query': 'OpenAI'}),
        ('webpage', {'query': 'https://openclaw.ai'}),
        ('lens', {'query': 'https://openclaw.ai/favicon.ico'}),
        ('maps-reviews', {'query': 'coffee shanghai', 'pick': 1}),
        ('maps-reviews-pick2', {'query': 'coffee shanghai', 'pick': 2}),
        ('maps-reviews-all', {'query': 'coffee shanghai'}),
    ]

    if full:
        checks.extend([
            ('videos', {'query': 'OpenAI'}),
            ('places', {'query': 'coffee shanghai'}),
            ('shopping', {'query': 'RTX 5090'}),
            ('scholar', {'query': 'retrieval augmented generation'}),
        ])

    for endpoint, spec in checks:
        summary['endpointsTested'].append(endpoint)
        query = spec.get('query')
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
                record_result(summary, endpoint, payload, ok=result.get('ok', False))
                continue

            if endpoint.startswith('maps-reviews'):
                result = run_maps_reviews(query, num=3, page=1, gl='us', hl='en', pick=spec.get('pick', 1))
                payload = {
                    'ok': result.get('ok', False),
                    'query': query,
                    'pick': spec.get('pick', 1),
                    'selectedPlace': result.get('selectedPlace'),
                    'mapsShape': summarize_response_shape(result.get('maps', {})),
                    'reviewsShape': summarize_response_shape(result.get('reviews', {})),
                }
                record_result(summary, endpoint, payload, ok=result.get('ok', False))
                continue

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

            record_result(summary, endpoint, payload, ok=payload.get('ok', False))
        except (SystemExit, SerperAPIError) as e:
            record_result(summary, endpoint, {
                'ok': False,
                'query': query,
                'error': f'API Error / Exit: {e}',
            }, ok=False)
        except Exception as e:
            record_result(summary, endpoint, {
                'ok': False,
                'query': query,
                'error': f'{type(e).__name__}: {e}',
            }, ok=False)

    negative_checks = [
        ('arg-conflict-json-raw', lambda: parse_search_args(['web', 'OpenAI', '--json', '--raw']), UsageError),
        ('reviews-missing-id', lambda: parse_search_args(['reviews']), UsageError),
        ('maps-reviews-all-pick-conflict', lambda: parse_search_args(['maps-reviews', 'coffee shanghai', '--all', '--pick', '2']), UsageError),
        ('webpage-missing-url', lambda: parse_search_args(['webpage']), UsageError),
        ('num-nonpositive', lambda: parse_search_args(['web', 'OpenAI', '--num', '0']), UsageError),
        ('page-nonpositive', lambda: parse_search_args(['web', 'OpenAI', '--page', '-1']), UsageError),
        ('limit-nonpositive', lambda: parse_search_args(['web', 'OpenAI', '--limit', '0']), UsageError),
        ('unknown-endpoint', lambda: parse_search_args(['newss']), UsageError),
    ]

    for name, fn, expected_exc in negative_checks:
        summary['endpointsTested'].append(name)
        try:
            fn()
            record_result(summary, name, {
                'ok': False,
                'error': f'Expected {expected_exc.__name__} but no exception was raised',
            }, ok=False)
        except expected_exc as e:
            record_result(summary, name, {
                'ok': True,
                'expectedError': expected_exc.__name__,
                'message': str(e),
            }, ok=True)
        except Exception as e:
            record_result(summary, name, {
                'ok': False,
                'error': f'Expected {expected_exc.__name__}, got {type(e).__name__}: {e}',
            }, ok=False)

    emit(summary, compact=compact)
    sys.exit(0 if summary['ok'] else 1)


if __name__ == '__main__':
    main()
