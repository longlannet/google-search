#!/usr/bin/env python3
from client import do_request
from io_common import safe_print
from renderers import print_places, print_reviews
from renderers_json import save_output, serialize_json


def _select_place_payload(chosen):
    return {
        'title': chosen.get('title'),
        'address': chosen.get('address'),
        'rating': chosen.get('rating'),
        'ratingCount': chosen.get('ratingCount'),
        'website': chosen.get('website'),
        'placeId': chosen.get('placeId'),
        'cid': chosen.get('cid'),
        'fid': chosen.get('fid'),
    }


def run_maps_reviews(query, num=5, page=1, gl='cn', hl='zh-cn', pick=1):
    maps_data, maps_key = do_request('maps', query, num, page, gl, hl)
    places = maps_data.get('places', [])
    if not places:
        return {
            'ok': False,
            'query': query,
            'pick': pick,
            'maps': maps_data,
            'selectedPlace': None,
            'reviews': None,
            'error': 'No places found from maps query',
            'usedKeySuffixes': {'maps': maps_key[-4:]},
        }

    index = max(1, pick) - 1
    if index >= len(places):
        return {
            'ok': False,
            'query': query,
            'pick': pick,
            'maps': maps_data,
            'selectedPlace': None,
            'reviews': None,
            'error': f'Pick {pick} out of range; only {len(places)} places found',
            'usedKeySuffixes': {'maps': maps_key[-4:]},
        }

    chosen = places[index]
    reviews_data, reviews_key = do_request(
        'reviews',
        query=chosen.get('title') or query,
        num=num,
        page=page,
        gl=gl,
        hl=hl,
        place_id=chosen.get('placeId'),
        cid=chosen.get('cid'),
        fid=chosen.get('fid'),
    )

    return {
        'ok': True,
        'query': query,
        'pick': pick,
        'maps': maps_data,
        'selectedPlace': _select_place_payload(chosen),
        'reviews': reviews_data,
        'usedKeySuffixes': {'maps': maps_key[-4:], 'reviews': reviews_key[-4:]},
    }


def run_maps_reviews_all(query, num=5, page=1, gl='cn', hl='zh-cn'):
    maps_data, maps_key = do_request('maps', query, num, page, gl, hl)
    places = maps_data.get('places', [])
    if not places:
        return {
            'ok': False,
            'allSucceeded': False,
            'failedCount': 0,
            'query': query,
            'maps': maps_data,
            'results': [],
            'error': 'No places found from maps query',
            'usedKeySuffixes': {'maps': maps_key[-4:]},
        }

    results = []
    used_review_keys = []
    failed_count = 0
    for idx, chosen in enumerate(places, start=1):
        try:
            reviews_data, reviews_key = do_request(
                'reviews',
                query=chosen.get('title') or query,
                num=num,
                page=page,
                gl=gl,
                hl=hl,
                place_id=chosen.get('placeId'),
                cid=chosen.get('cid'),
                fid=chosen.get('fid'),
            )
            used_review_keys.append(reviews_key[-4:])
            results.append({
                'ok': True,
                'pick': idx,
                'selectedPlace': _select_place_payload(chosen),
                'reviews': reviews_data,
            })
        except Exception as e:
            failed_count += 1
            results.append({
                'ok': False,
                'pick': idx,
                'selectedPlace': _select_place_payload(chosen),
                'error': f'{type(e).__name__}: {e}',
                'reviews': None,
            })

    all_succeeded = failed_count == 0
    return {
        'ok': True,
        'allSucceeded': all_succeeded,
        'failedCount': failed_count,
        'query': query,
        'maps': maps_data,
        'results': results,
        'usedKeySuffixes': {'maps': maps_key[-4:], 'reviews': used_review_keys},
    }


def emit_maps_reviews_json(result, compact=False, save_path=None):
    text = serialize_json(result, compact=compact)
    if save_path:
        save_output(text, save_path)
    safe_print(text)


def emit_maps_reviews_raw(result, compact=False, save_path=None):
    raw_payload = {
        'maps': result.get('maps'),
        'reviews': result.get('reviews'),
    }
    text = serialize_json(raw_payload, compact=compact)
    if save_path:
        save_output(text, save_path)
    safe_print(text)


def emit_maps_reviews_all_json(result, compact=False, save_path=None):
    text = serialize_json(result, compact=compact)
    if save_path:
        save_output(text, save_path)
    safe_print(text)


def emit_maps_reviews_all_raw(result, compact=False, save_path=None):
    raw_payload = {
        'maps': result.get('maps'),
        'results': result.get('results'),
    }
    text = serialize_json(raw_payload, compact=compact)
    if save_path:
        save_output(text, save_path)
    safe_print(text)


def render_maps_reviews_pretty(result, pick, gl, hl, limit=10):
    query = result.get('query', '')
    safe_print(f'🔍 Google (Serper) maps → reviews 联动: {query}...')
    if not result.get('ok'):
        safe_print(f"❌ {result.get('error', 'maps-reviews failed')}")
        return

    selected = result.get('selectedPlace') or {}
    safe_print(f'🎯 已选择第 {pick} 个地点进行评论抓取:')
    print_places([selected], limit=1, title='目标地点', show_ids=True)
    reviews = (result.get('reviews') or {}).get('reviews', []) or (result.get('reviews') or {}).get('organic', [])
    print_reviews(reviews, limit=limit)
    suffixes = result.get('usedKeySuffixes', {})
    safe_print(f"📡 数据来源: Google (via Serper.dev) | endpoint=maps-reviews | mapsKey=...{suffixes.get('maps','?')} | reviewsKey=...{suffixes.get('reviews','?')} | gl={gl} hl={hl}")


def render_maps_reviews_all_pretty(result, gl, hl, limit=5):
    query = result.get('query', '')
    safe_print(f'🔍 Google (Serper) maps → reviews 全量联动: {query}...')
    if not result.get('ok'):
        safe_print(f"❌ {result.get('error', 'maps-reviews --all failed')}")
        return

    failed_count = result.get('failedCount', 0)
    all_succeeded = result.get('allSucceeded', failed_count == 0)
    status_text = '全部成功' if all_succeeded else f'部分失败（失败 {failed_count} 个）'
    safe_print(f'📊 执行状态: {status_text}')

    for entry in result.get('results', []):
        pick = entry.get('pick')
        place = entry.get('selectedPlace') or {}
        safe_print(f'\n🎯 第 {pick} 个地点:')
        print_places([place], limit=1, title='目标地点', show_ids=True)
        if not entry.get('ok'):
            safe_print(f"❌ 评论抓取失败: {entry.get('error', 'unknown error')}")
            continue
        reviews = (entry.get('reviews') or {}).get('reviews', []) or (entry.get('reviews') or {}).get('organic', [])
        print_reviews(reviews, limit=limit)

    suffixes = result.get('usedKeySuffixes', {})
    safe_print(f"📡 数据来源: Google (via Serper.dev) | endpoint=maps-reviews --all | mapsKey=...{suffixes.get('maps','?')} | reviewKeys={suffixes.get('reviews', [])} | gl={gl} hl={hl}")
