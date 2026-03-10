#!/usr/bin/env python3
import json
import os
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SCRIPT_DIR.parent / 'config'
ENV_FILE = CONFIG_DIR / 'serper.env'
RUNTIME_DIR = SCRIPT_DIR.parent / 'runtime'
RR_INDEX_FILE = RUNTIME_DIR / 'serper_rr.idx'


class SerperAPIError(Exception):
    pass


_session = requests.Session()


def load_api_keys():
    keys = []
    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text('utf-8').splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue

            if line.startswith('SERPER_API_KEY='):
                line = line.split('=', 1)[1].strip().strip('"').strip("'")
            elif line.lower().startswith('key:'):
                line = line.split(':', 1)[1].strip()

            if len(line) > 20:
                keys.append(line)

    env_key = os.environ.get('SERPER_API_KEY')
    if not keys and env_key and len(env_key) > 20:
        keys.append(env_key)

    deduped = []
    seen = set()
    for key in keys:
        if key not in seen:
            deduped.append(key)
            seen.add(key)
    return deduped


def get_next_key_index(total_keys):
    idx = 0
    try:
        import fcntl
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with open(RR_INDEX_FILE, 'a+') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            txt = f.read().strip()
            if txt:
                try:
                    idx = int(txt)
                except ValueError:
                    idx = 0
            current_idx = idx % total_keys
            next_idx = (current_idx + 1) % total_keys
            f.seek(0)
            f.truncate()
            f.write(str(next_idx))
            fcntl.flock(f, fcntl.LOCK_UN)
            return current_idx
    except Exception:
        pass
    return 0


def do_request(endpoint, query, num, page=1, gl='cn', hl='zh-cn', place_id=None, cid=None, fid=None):
    keys = load_api_keys()
    if not keys:
        raise SerperAPIError('No valid API keys found in config/serper.env')

    url = f'https://google.serper.dev/{endpoint}'
    payload_obj = {}

    if endpoint in {'webpage', 'lens'}:
        payload_obj['url'] = query
        if endpoint == 'lens':
            payload_obj['num'] = num
            payload_obj['page'] = page
    else:
        payload_obj.update({
            'q': query,
            'num': num,
            'page': page,
            'gl': gl,
            'hl': hl,
        })

    if place_id:
        payload_obj['placeId'] = place_id
    if cid:
        payload_obj['cid'] = cid
    if fid:
        payload_obj['fid'] = fid

    payload = json.dumps(payload_obj)
    total = len(keys)
    start_idx = get_next_key_index(total)
    ordered_keys = keys[start_idx:] + keys[:start_idx]

    last_error = None
    errors = []

    for key in ordered_keys:
        headers = {
            'X-API-KEY': key,
            'Content-Type': 'application/json'
        }
        try:
            response = _session.post(url, headers=headers, data=payload, timeout=20)
            if response.status_code == 200:
                try:
                    return response.json(), key
                except ValueError as e:
                    last_error = f'Invalid JSON response: {e}'
                    errors.append(f'...{key[-4:]} => Invalid JSON response')
                    continue
            if response.status_code in [401, 403, 429]:
                errors.append(f'...{key[-4:]} => HTTP {response.status_code}')
                last_error = f'HTTP {response.status_code}'
                continue
            response.raise_for_status()
        except Exception as e:
            last_error = e
            errors.append(f'...{key[-4:]} => {type(e).__name__}: {e}')
            continue

    error_summary = '; '.join(errors[-10:]) if errors else str(last_error)
    raise SerperAPIError(f'All API Keys failed. Last error: {last_error} | Summary: {error_summary}')
