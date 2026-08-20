from args import MAX_MAPS_ALL_PLACES
from client import SerperAPIError, do_request
from io_common import safe_print, sanitize_external_data, sanitize_external_text
from renderers import print_places, print_reviews
from renderers_json import save_output, serialize_json


class WorkflowValidationError(ValueError):
    pass


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


def _review_identifier_kwargs(chosen):
    for source_name, target_name in (('placeId', 'place_id'), ('cid', 'cid'), ('fid', 'fid')):
        value = chosen.get(source_name)
        if value:
            return {target_name: value}
    return {}


def _places_from_maps(maps_data):
    if not isinstance(maps_data, dict):
        return []
    candidates = maps_data.get('places')
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def _workflow_error_payload(error):
    error_type = type(error).__name__
    if getattr(error, 'kind', None):
        error_type = f'{error_type}:{error.kind}'
    if isinstance(error, (SerperAPIError, WorkflowValidationError)):
        message = sanitize_external_text(str(error), max_chars=500)
    else:
        message = 'Unexpected workflow error'
    return {
        'errorType': error_type,
        'errorMessage': message,
        'error': f'{error_type}: {message}',
    }


def _base_failure(query, maps_data, maps_slot, error, **extra):
    payload = {
        'ok': False,
        'query': query,
        'maps': maps_data,
        'error': error,
        'usedKeySlots': {'maps': maps_slot},
    }
    payload.update(extra)
    return payload


def run_maps_reviews(query, num=5, page=1, gl='cn', hl='zh-cn', pick=1):
    maps_data, maps_slot = do_request('maps', query, num, page, gl, hl)
    all_places = _places_from_maps(maps_data)
    places = all_places[:num]
    if not places:
        return _base_failure(
            query, maps_data, maps_slot, 'No places found from maps query',
            pick=pick, selectedPlace=None, reviews=None,
            mapsPlaceCount=len(all_places), consideredPlaceCount=len(places),
        )
    index = pick - 1
    if index < 0 or index >= len(places):
        return _base_failure(
            query, maps_data, maps_slot,
            f'Pick {pick} out of range; only {len(places)} places found',
            pick=pick, selectedPlace=None, reviews=None,
            mapsPlaceCount=len(all_places), consideredPlaceCount=len(places),
        )

    chosen = places[index]
    identifier_kwargs = _review_identifier_kwargs(chosen)
    if not identifier_kwargs:
        return _base_failure(
            query, maps_data, maps_slot, 'Selected place has no supported review identifier',
            pick=pick, selectedPlace=_select_place_payload(chosen), reviews=None,
            mapsPlaceCount=len(all_places), consideredPlaceCount=len(places),
        )
    reviews_data, reviews_slot = do_request(
        'reviews', query=query, num=num, page=page, gl=gl, hl=hl,
        **identifier_kwargs,
    )
    return {
        'ok': True,
        'query': query,
        'pick': pick,
        'maps': maps_data,
        'selectedPlace': _select_place_payload(chosen),
        'reviews': reviews_data,
        'mapsPlaceCount': len(all_places),
        'consideredPlaceCount': len(places),
        'truncatedCount': len(all_places) - len(places),
        'usedKeySlots': {'maps': maps_slot, 'reviews': reviews_slot},
    }


def run_maps_reviews_all(query, num=5, page=1, gl='cn', hl='zh-cn'):
    if not isinstance(num, int) or isinstance(num, bool) or not 1 <= num <= MAX_MAPS_ALL_PLACES:
        raise ValueError(f'num must be between 1 and {MAX_MAPS_ALL_PLACES} for maps-reviews --all')
    maps_data, maps_slot = do_request('maps', query, num, page, gl, hl)
    all_places = _places_from_maps(maps_data)
    places = all_places[:num]
    if not places:
        return _base_failure(
            query, maps_data, maps_slot, 'No places found from maps query',
            allSucceeded=False, failedCount=0, attemptedCount=0, skippedCount=0, results=[],
            mapsPlaceCount=len(all_places), consideredPlaceCount=0, truncatedCount=0,
        )

    results = []
    review_slots = []
    for index, chosen in enumerate(places, start=1):
        try:
            identifier_kwargs = _review_identifier_kwargs(chosen)
            if not identifier_kwargs:
                raise WorkflowValidationError('Selected place has no supported review identifier')
            reviews_data, reviews_slot = do_request(
                'reviews', query=query, num=num, page=page, gl=gl, hl=hl,
                **identifier_kwargs,
            )
            review_slots.append(reviews_slot)
            results.append({
                'ok': True,
                'pick': index,
                'selectedPlace': _select_place_payload(chosen),
                'reviews': reviews_data,
            })
        except Exception as error:
            results.append({
                'ok': False,
                'pick': index,
                'selectedPlace': _select_place_payload(chosen),
                'reviews': None,
                **_workflow_error_payload(error),
            })
            return {
                'ok': False,
                'allSucceeded': False,
                'failedCount': 1,
                'attemptedCount': len(results),
                'skippedCount': len(places) - index,
                'query': query,
                'maps': maps_data,
                'results': results,
                'error': f'Review retrieval stopped at place {index}',
                'mapsPlaceCount': len(all_places),
                'consideredPlaceCount': len(places),
                'truncatedCount': len(all_places) - len(places),
                'usedKeySlots': {'maps': maps_slot, 'reviews': review_slots},
            }

    return {
        'ok': True,
        'allSucceeded': True,
        'failedCount': 0,
        'attemptedCount': len(results),
        'skippedCount': 0,
        'query': query,
        'maps': maps_data,
        'results': results,
        'mapsPlaceCount': len(all_places),
        'consideredPlaceCount': len(places),
        'truncatedCount': len(all_places) - len(places),
        'usedKeySlots': {'maps': maps_slot, 'reviews': review_slots},
    }


def _emit_payload(payload, compact=False, save_path=None):
    text = serialize_json(sanitize_external_data(payload), compact=compact)
    if save_path is not None:
        save_output(text, save_path)
    safe_print(text)


def emit_maps_reviews_json(result, compact=False, save_path=None):
    _emit_payload({'trust': 'untrusted_external_content', **result}, compact, save_path)


def emit_maps_reviews_raw(result, compact=False, save_path=None):
    raw_payload = {
        'ok': result.get('ok', False),
        'maps': result.get('maps'),
        'reviews': result.get('reviews'),
        'error': result.get('error'),
    }
    _emit_payload(raw_payload, compact, save_path)


def emit_maps_reviews_all_json(result, compact=False, save_path=None):
    _emit_payload({'trust': 'untrusted_external_content', **result}, compact, save_path)


def emit_maps_reviews_all_raw(result, compact=False, save_path=None):
    raw_payload = {
        'ok': result.get('ok', False),
        'allSucceeded': result.get('allSucceeded', False),
        'failedCount': result.get('failedCount', 0),
        'maps': result.get('maps'),
        'results': result.get('results'),
        'error': result.get('error'),
    }
    _emit_payload(raw_payload, compact, save_path)


def render_maps_reviews_pretty(result, pick, gl, hl, limit=10):
    result = sanitize_external_data(result)
    query = result.get('query', '')
    safe_print(f'Google (Serper) maps -> reviews: {query}')
    if not result.get('ok'):
        safe_print(f"Request failed: {result.get('error', 'maps-reviews failed')}")
        return
    selected = result.get('selectedPlace') or {}
    safe_print(f'Selected place {pick}:')
    print_places([selected], limit=1, title='Target place', show_ids=True)
    reviews_payload = result.get('reviews') if isinstance(result.get('reviews'), dict) else {}
    reviews = reviews_payload.get('reviews', []) or reviews_payload.get('organic', [])
    print_reviews(reviews, limit=limit)
    safe_print(f'Data source: Google (via Serper.dev) | endpoint=maps-reviews | gl={gl} hl={hl}')


def render_maps_reviews_all_pretty(result, gl, hl, limit=5):
    result = sanitize_external_data(result)
    query = result.get('query', '')
    safe_print(f'Google (Serper) maps -> reviews --all: {query}')
    if not result.get('ok'):
        safe_print(f"Request failed: {result.get('error', 'maps-reviews --all failed')}")
    failed_count = result.get('failedCount', 0)
    status_text = 'all succeeded' if result.get('allSucceeded') else f'failed/stopped ({failed_count} failed)'
    safe_print(f'Status: {status_text}')
    for entry in result.get('results', []):
        if not isinstance(entry, dict):
            continue
        pick = entry.get('pick')
        place = entry.get('selectedPlace') or {}
        safe_print(f'Place {pick}:')
        print_places([place], limit=1, title='Target place', show_ids=True)
        if not entry.get('ok'):
            safe_print(f"Review request failed: {entry.get('error', 'unknown error')}")
            continue
        reviews_payload = entry.get('reviews') if isinstance(entry.get('reviews'), dict) else {}
        reviews = reviews_payload.get('reviews', []) or reviews_payload.get('organic', [])
        print_reviews(reviews, limit=limit)
    safe_print(f'Data source: Google (via Serper.dev) | endpoint=maps-reviews --all | gl={gl} hl={hl}')
