#!/usr/bin/env python3
import json
import sys

from client import SerperAPIError, do_request, load_api_keys
from response_shapes import summarize_response_shape


NOTES = [
    '轻量联网检查：验证最小可用链路，不做全量端点巡检。',
    '默认只测试 search endpoint，避免安装阶段因全量自检过慢而误判失败。',
]


def emit(payload, compact=False):
    if compact:
        print(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv):
    compact = '--compact' in argv
    query = 'OpenClaw'
    keys = load_api_keys()
    summary = {
        'ok': True,
        'kind': 'smoke-test',
        'endpoint': 'search',
        'query': query,
        'keyCount': len(keys),
        'notes': NOTES,
    }

    if not keys:
        summary['ok'] = False
        summary['error'] = 'No valid API keys found in config/serper.env or SERPER_API_KEY'
        emit(summary, compact)
        sys.exit(1)

    try:
        data, used_key = do_request('search', query, num=3, page=1, gl='us', hl='en')
        organic = data.get('organic') or []
        if not organic:
            summary['ok'] = False
            summary['error'] = 'search response does not include organic results'
        summary['usedKeySuffix'] = used_key[-4:]
        summary['shape'] = summarize_response_shape(data)
        summary['organicCount'] = len(organic)
    except SerperAPIError as e:
        summary['ok'] = False
        summary['error'] = str(e)
    except Exception as e:
        summary['ok'] = False
        summary['error'] = f'{type(e).__name__}: {e}'

    emit(summary, compact)
    sys.exit(0 if summary['ok'] else 1)


if __name__ == '__main__':
    main(sys.argv[1:])
