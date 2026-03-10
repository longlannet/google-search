#!/usr/bin/env python3
import sys

from args import UsageError, parse_args
from client import SerperAPIError, do_request
from helptext import print_examples, print_overview
from io_common import safe_print
from renderers_json import emit_json_wrapper, emit_raw_json, save_output, serialize_json
from renderers_pretty import render_results
from workflows import (
    emit_maps_reviews_all_json,
    emit_maps_reviews_all_raw,
    emit_maps_reviews_json,
    emit_maps_reviews_raw,
    render_maps_reviews_all_pretty,
    render_maps_reviews_pretty,
    run_maps_reviews,
    run_maps_reviews_all,
)


def _emit_error(output_mode, endpoint, error_text, compact=False, save_path=None):
    if output_mode == 'json':
        payload = {'ok': False, 'endpoint': endpoint, 'error': error_text}
        text = serialize_json(payload, compact=compact)
        if save_path:
            save_output(text, save_path)
        safe_print(text)
        return

    if output_mode == 'raw':
        text = serialize_json({'error': error_text}, compact=compact)
        if save_path:
            save_output(text, save_path)
        safe_print(text)
        return

    safe_print(f"❌ 请求失败: {error_text}")


def main():
    try:
        args = parse_args(sys.argv[1:])
    except UsageError as e:
        safe_print(e)
        sys.exit(1)

    endpoint = args['endpoint']
    query = args['query']
    num = args['num']
    page = args['page']
    gl = args['gl']
    hl = args['hl']
    output_mode = args['output_mode']
    compact = args['compact']
    save_path = args['save_path']
    place_id = args['place_id']
    cid = args['cid']
    fid = args['fid']
    pick = args['pick']
    limit = args['limit']
    all_results = args['all_results']

    if endpoint == 'overview':
        print_overview()
        return
    if endpoint == 'examples':
        print_examples()
        return

    if endpoint == 'maps-reviews':
        if all_results:
            result = run_maps_reviews_all(query, num=num, page=page, gl=gl, hl=hl)
            if output_mode == 'json':
                emit_maps_reviews_all_json(result, compact=compact, save_path=save_path)
                return
            if output_mode == 'raw':
                emit_maps_reviews_all_raw(result, compact=compact, save_path=save_path)
                return
            render_maps_reviews_all_pretty(result, gl=gl, hl=hl, limit=limit)
            return

        result = run_maps_reviews(query, num=num, page=page, gl=gl, hl=hl, pick=pick)
        if output_mode == 'json':
            emit_maps_reviews_json(result, compact=compact, save_path=save_path)
            return
        if output_mode == 'raw':
            emit_maps_reviews_raw(result, compact=compact, save_path=save_path)
            return
        render_maps_reviews_pretty(result, pick=pick, gl=gl, hl=hl, limit=limit)
        return

    try:
        data, used_key = do_request(endpoint, query, num, page, gl, hl, place_id=place_id, cid=cid, fid=fid)
    except SerperAPIError as e:
        _emit_error(output_mode, endpoint, str(e), compact=compact, save_path=save_path)
        sys.exit(1)

    if output_mode == 'raw':
        emit_raw_json(data, compact=compact, save_path=save_path)
        return

    if output_mode == 'json':
        emit_json_wrapper(endpoint, data, used_key, gl, hl, query, num, page, compact=compact, save_path=save_path)
        return

    safe_print(f'🔍 Google (Serper) {endpoint} 搜索: {query} (Page {page})...')
    render_results(endpoint, data, limit=limit)
    safe_print(f'📡 数据来源: Google (via Serper.dev) | endpoint={endpoint} | gl={gl} hl={hl}')


if __name__ == '__main__':
    main()
