#!/usr/bin/env python3
import argparse

ENDPOINTS = {
    'web': 'search',
    'search': 'search',
    'images': 'images',
    'image': 'images',
    'news': 'news',
    'videos': 'videos',
    'video': 'videos',
    'places': 'places',
    'place': 'places',
    'maps': 'maps',
    'map': 'maps',
    'reviews': 'reviews',
    'review': 'reviews',
    'autocomplete': 'autocomplete',
    'suggest': 'autocomplete',
    'shopping': 'shopping',
    'scholar': 'scholar',
    'patents': 'patents',
    'patent': 'patents',
    'webpage': 'webpage',
    'page': 'webpage',
    'lens': 'lens',
    'maps-reviews': 'maps-reviews',
    'map-reviews': 'maps-reviews',
    'overview': 'overview',
    'cheatsheet': 'overview',
    'quickref': 'overview',
    'help': 'overview',
    'examples': 'examples',
}


class UsageError(Exception):
    pass


def build_parser():
    parser = argparse.ArgumentParser(add_help=True, prog='search.py')
    parser.add_argument('mode', nargs='?', help='Search mode / endpoint')
    parser.add_argument('query', nargs='?', help='Search query or URL depending on endpoint')
    parser.add_argument('num_pos', nargs='?', help='Legacy positional num')
    parser.add_argument('page_pos', nargs='?', help='Legacy positional page')
    parser.add_argument('gl_pos', nargs='?', help='Legacy positional gl')
    parser.add_argument('hl_pos', nargs='?', help='Legacy positional hl')

    parser.add_argument('--num', '-n', type=int, default=None)
    parser.add_argument('--page', '-p', type=int, default=None)
    parser.add_argument('--gl', '-g', default=None)
    parser.add_argument('--hl', '-l', default=None)
    parser.add_argument('--pick', type=int, default=1)
    parser.add_argument('--limit', type=int, default=10, help='Pretty output limit for result rows')
    parser.add_argument('--all', dest='all_results', action='store_true', help='For maps-reviews: fetch reviews for all returned places')
    parser.add_argument('--place-id', dest='place_id', default=None)
    parser.add_argument('--cid', default=None)
    parser.add_argument('--fid', default=None)
    parser.add_argument('--json', dest='json_mode', action='store_true')
    parser.add_argument('--raw', dest='raw_mode', action='store_true')
    parser.add_argument('--compact', action='store_true')
    parser.add_argument('--save', dest='save_path', default=None)
    return parser


def get_usage():
    return build_parser().format_help()


def _coerce_int(value, default):
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def parse_args(argv):
    if not argv:
        raise UsageError(get_usage())

    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit:
        raise UsageError(get_usage())

    if not ns.mode:
        raise UsageError(get_usage())

    mode_lower = ns.mode.lower()
    endpoint = ENDPOINTS.get(mode_lower)

    if endpoint is None:
        endpoint = 'search'
        query = ns.mode
        num = ns.num if ns.num is not None else _coerce_int(ns.query, 5)
        page = ns.page if ns.page is not None else _coerce_int(ns.num_pos, 1)
        gl = ns.gl if ns.gl is not None else (ns.page_pos or 'cn')
        hl = ns.hl if ns.hl is not None else (ns.gl_pos or 'zh-cn')
    else:
        query = ns.query
        num = ns.num if ns.num is not None else _coerce_int(ns.num_pos, 5)
        page = ns.page if ns.page is not None else _coerce_int(ns.page_pos, 1)
        gl = ns.gl if ns.gl is not None else (ns.gl_pos or 'cn')
        hl = ns.hl if ns.hl is not None else (ns.hl_pos or 'zh-cn')

    if endpoint in {'overview', 'examples'}:
        query = query or endpoint
    elif endpoint == 'reviews':
        if not (ns.place_id or ns.cid or ns.fid):
            raise UsageError('reviews endpoint requires one of: --place-id, --cid, or --fid')
        if not query:
            query = ns.place_id or ns.cid or ns.fid
    elif endpoint in {'webpage', 'lens'}:
        if not query:
            raise UsageError(f'{endpoint} endpoint requires a URL')
    elif not query:
        raise UsageError(get_usage())

    if endpoint == 'maps-reviews' and ns.all_results and ns.pick != 1:
        raise UsageError('--pick cannot be combined with --all for maps-reviews')

    if ns.raw_mode and ns.json_mode:
        raise UsageError('--json cannot be combined with --raw')

    output_mode = 'pretty'
    if ns.raw_mode:
        output_mode = 'raw'
    elif ns.json_mode:
        output_mode = 'json'

    return {
        'endpoint': endpoint,
        'query': query,
        'num': num,
        'page': page,
        'gl': gl,
        'hl': hl,
        'output_mode': output_mode,
        'compact': ns.compact,
        'save_path': ns.save_path,
        'place_id': ns.place_id,
        'cid': ns.cid,
        'fid': ns.fid,
        'pick': ns.pick,
        'limit': ns.limit,
        'all_results': ns.all_results,
    }
