import json

from io_common import safe_print, sanitize_external_data, sanitize_external_text
from secure_io import MAX_OUTPUT_BYTES, OutputSecurityError, secure_write_text


def serialize_json(payload, compact=False):
    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    # Emitters add one stdout newline, so reserve it in the complete-output cap.
    if len(text.encode('utf-8')) + 1 > MAX_OUTPUT_BYTES:
        raise OutputSecurityError(f'JSON output exceeds the {MAX_OUTPUT_BYTES}-byte limit')
    return text


def save_output(text, save_path):
    return secure_write_text(text, save_path)


def _request_summary(endpoint, query, num, page, gl, hl, place_id=None, cid=None, fid=None):
    if endpoint == 'webpage':
        return {'url': sanitize_external_text(query)}
    if endpoint == 'lens':
        return {'url': sanitize_external_text(query), 'gl': gl, 'hl': hl}
    if endpoint == 'reviews':
        identifiers = {'placeId': place_id, 'cid': cid, 'fid': fid}
        selected = {name: sanitize_external_text(value) for name, value in identifiers.items() if value}
        return {**selected, 'gl': gl, 'hl': hl}
    if endpoint == 'maps':
        return {'q': sanitize_external_text(query), 'hl': hl, 'page': page}
    if endpoint == 'autocomplete':
        return {'q': sanitize_external_text(query), 'gl': gl, 'hl': hl}
    return {
        'q': sanitize_external_text(query), 'num': num, 'page': page, 'gl': gl, 'hl': hl,
    }


def emit_json_wrapper(
    endpoint, data, key_slot, gl, hl, query, num, page, compact=False, save_path=None,
    place_id=None, cid=None, fid=None,
):
    payload = {
        'ok': True,
        'trust': 'untrusted_external_content',
        'endpoint': endpoint,
        'keySlot': key_slot,
        'request': _request_summary(
            endpoint, query, num, page, gl, hl, place_id=place_id, cid=cid, fid=fid,
        ),
        'response': sanitize_external_data(data),
    }
    text = serialize_json(payload, compact=compact)
    if save_path is not None:
        save_output(text, save_path)
    safe_print(text)


def emit_raw_json(data, compact=False, save_path=None):
    text = serialize_json(sanitize_external_data(data), compact=compact)
    if save_path is not None:
        save_output(text, save_path)
    safe_print(text)
