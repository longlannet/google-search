#!/usr/bin/env python3
import json
import sys

from utils import SerperAPIError, do_request, load_api_keys, run_maps_reviews, run_maps_reviews_all, safe_print, summarize_response_shape


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


def main():
    args = parse_args(sys.argv[1:])
    full = args['full']
    compact = args['compact']

    keys = load_api_keys()
    summary = {
        'ok': True,
        'mode': 'full' if full else 'basic',
        'keyCount': len(keys),
        'endpointsTested': [],
        'results': {},
        'errors': [],
    }

    if not keys:
        summary['ok'] = False
        summary['errors'].append('No valid API keys found in config/serper.env')
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
                summary['results'][endpoint] = {
                    'ok': result.get('ok', False),
                    'query': query,
                    'resultCount': len(result.get('results', [])),
                    'mapsShape': summarize_response_shape(result.get('maps', {})),
                }
                if not result.get('ok', False):
                    summary['ok'] = False
                    summary['errors'].append(f'{endpoint}: {result.get("error", "failed")}')
                continue

            if endpoint.startswith('maps-reviews'):
                result = run_maps_reviews(query, num=3, page=1, gl='us', hl='en', pick=spec.get('pick', 1))
                summary['results'][endpoint] = {
                    'ok': result.get('ok', False),
                    'query': query,
                    'pick': spec.get('pick', 1),
                    'selectedPlace': result.get('selectedPlace'),
                    'mapsShape': summarize_response_shape(result.get('maps', {})),
                    'reviewsShape': summarize_response_shape(result.get('reviews', {})),
                }
                if not result.get('ok', False):
                    summary['ok'] = False
                    summary['errors'].append(f'{endpoint}: {result.get("error", "failed")}')
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
            summary['results'][endpoint] = {
                'ok': True,
                'query': query,
                'usedKeySuffix': used_key[-4:],
                'shape': summarize_response_shape(data),
            }
        except (SystemExit, SerperAPIError) as e:
            summary['ok'] = False
            summary['results'][endpoint] = {
                'ok': False,
                'query': query,
                'error': f'API Error / Exit: {e}',
            }
            summary['errors'].append(f'{endpoint}: {e}')
        except Exception as e:
            summary['ok'] = False
            summary['results'][endpoint] = {
                'ok': False,
                'query': query,
                'error': f'{type(e).__name__}: {e}',
            }
            summary['errors'].append(f'{endpoint}: {type(e).__name__}: {e}')

    emit(summary, compact=compact)
    sys.exit(0 if summary['ok'] else 1)


if __name__ == '__main__':
    main()
