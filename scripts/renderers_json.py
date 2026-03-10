#!/usr/bin/env python3
import json
from pathlib import Path

from io_common import safe_print


def serialize_json(payload, compact=False):
    if compact:
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return json.dumps(payload, ensure_ascii=False, indent=2)


def save_output(text, save_path):
    path = Path(save_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def emit_json_wrapper(endpoint, data, used_key, gl, hl, query, num, page, compact=False, save_path=None):
    payload = {
        'ok': True,
        'endpoint': endpoint,
        'query': query,
        'num': num,
        'page': page,
        'gl': gl,
        'hl': hl,
        'usedKeySuffix': used_key[-4:],
        'response': data,
    }
    text = serialize_json(payload, compact=compact)
    if save_path:
        save_output(text, save_path)
    safe_print(text)


def emit_raw_json(data, compact=False, save_path=None):
    text = serialize_json(data, compact=compact)
    if save_path:
        save_output(text, save_path)
    safe_print(text)
