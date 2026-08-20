import argparse
import ipaddress
import re
import socket
import unicodedata
from urllib.parse import parse_qsl, unquote_plus, urlsplit


ENDPOINTS = {
    'web': 'search', 'search': 'search', 'images': 'images', 'image': 'images',
    'news': 'news', 'videos': 'videos', 'video': 'videos', 'places': 'places',
    'place': 'places', 'maps': 'maps', 'map': 'maps', 'reviews': 'reviews',
    'review': 'reviews', 'autocomplete': 'autocomplete', 'suggest': 'autocomplete',
    'shopping': 'shopping', 'scholar': 'scholar', 'patents': 'patents',
    'patent': 'patents', 'webpage': 'webpage', 'page': 'webpage', 'lens': 'lens',
    'maps-reviews': 'maps-reviews', 'map-reviews': 'maps-reviews',
    'overview': 'overview', 'cheatsheet': 'overview', 'quickref': 'overview',
    'help': 'overview', 'examples': 'examples',
}
LEGACY_SEARCH_ALIASES = {'search', 'web'}
DEFAULT_GL = 'cn'
DEFAULT_HL = 'zh-cn'
MAX_NUM = 100
MAX_PAGE = 100
MAX_PICK = 20
MAX_LIMIT = 100
MAX_QUERY_LENGTH = 2_048
MAX_IDENTIFIER_LENGTH = 512
MAX_MAPS_ALL_PLACES = 10
MAX_URL_QUERY_FIELDS = 100
LOCALE_PATTERN = re.compile(r'^[A-Za-z0-9]{2,8}(?:-[A-Za-z0-9]{1,8})?$')
BLOCKED_HOST_SUFFIXES = ('.localhost', '.local', '.internal', '.home.arpa')
SENSITIVE_QUERY_KEYS = {
    'token', 'idtoken', 'refreshtoken', 'authtoken', 'oauthtoken', 'apikey',
    'accesskey', 'accesskeyid', 'awsaccesskeyid', 'accesstoken', 'securitytoken',
    'auth', 'authorization',
    'bearer', 'credential', 'key', 'password', 'passwd', 'secret', 'signature',
    'sig', 'session', 'sessionid', 'sessiontoken',
    # Azure shared-access-signature fields.
    'se', 'sip', 'sp', 'spr', 'sr', 'st', 'sv', 'skoid', 'sktid', 'skt', 'ske', 'sks', 'skv',
}
SENSITIVE_QUERY_SUFFIXES = (
    'token', 'secret', 'signature', 'sessionid', 'apikey', 'accesskey', 'password', 'credential',
)
BIDI_CONTROLS = {
    '\u061c', '\u200e', '\u200f', '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',
    '\u2066', '\u2067', '\u2068', '\u2069',
}


class UsageError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise UsageError(f'Invalid command-line arguments\n\n{self.format_help()}')


def build_parser():
    parser = _ArgumentParser(add_help=True, allow_abbrev=False, prog='search.py')
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
    parser.add_argument('--pick', type=int, default=None)
    parser.add_argument('--limit', type=int, default=None, help='Pretty output limit for result rows')
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


def _parse_positional_int(name, value, default):
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise UsageError(f'{name} must be an integer') from None


def _require_range(name, value, maximum):
    if value < 1 or value > maximum:
        raise UsageError(f'{name} must be between 1 and {maximum}')


def _validate_plain_text(name, value, maximum, allow_empty=False):
    if value is None:
        return None
    if not isinstance(value, str):
        raise UsageError(f'{name} must be text')
    if (not value or not value.strip()) and not allow_empty:
        raise UsageError(f'{name} must not be empty')
    if len(value) > maximum:
        raise UsageError(f'{name} exceeds the {maximum}-character limit')
    if any(
        ord(char) < 0x20
        or 0x7F <= ord(char) <= 0x9F
        or 0xD800 <= ord(char) <= 0xDFFF
        or char in BIDI_CONTROLS
        for char in value
    ):
        raise UsageError(f'{name} contains prohibited control characters')
    return value


def _validate_locale(name, value):
    if not isinstance(value, str) or not LOCALE_PATTERN.fullmatch(value):
        raise UsageError(f'{name} must be a short alphanumeric locale code')
    return value


def _is_public_unicast_address(address):
    mapped_address = getattr(address, 'ipv4_mapped', None)
    if mapped_address is not None:
        return _is_public_unicast_address(mapped_address)
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or getattr(address, 'is_site_local', False)
    )


def _validate_global_address(address):
    try:
        parsed = ipaddress.ip_address(address.split('%', 1)[0])
    except ValueError:
        raise UsageError('URL host resolved to an invalid IP address') from None
    if not _is_public_unicast_address(parsed):
        raise UsageError('URL must resolve only to public unicast IP addresses')


def _is_sensitive_query_name(raw_name):
    lowered_name = unicodedata.normalize('NFKC', raw_name).strip().lower()
    canonical_name = re.sub(r'[^a-z0-9]', '', lowered_name)
    return (
        canonical_name in SENSITIVE_QUERY_KEYS
        or canonical_name.endswith(SENSITIVE_QUERY_SUFFIXES)
        or canonical_name.startswith(('xamz', 'xgoog', 'xms'))
    )


def _decoded_query_name_layers(raw_name):
    """Yield each decoding layer so nested percent-encoding cannot hide credentials."""
    current = raw_name
    for _ in range(16):
        yield current
        try:
            decoded = unquote_plus(current, errors='strict')
        except UnicodeDecodeError:
            raise UsageError('URL query contains an invalid encoded field name') from None
        if decoded == current:
            return
        current = decoded
    raise UsageError('URL query field name is excessively encoded')


def _validate_query_name(raw_name):
    maximum_fields = 1
    for decoded_name in _decoded_query_name_layers(raw_name):
        # A separator can itself be percent-encoded. Re-split every decoded
        # layer so ``token%26safe`` cannot collapse to the benign-looking
        # canonical name ``tokensafe``.
        normalized_name = unicodedata.normalize('NFKC', decoded_name)
        decoded_fields = re.split(r'[&;]', normalized_name)
        maximum_fields = max(maximum_fields, len(decoded_fields))
        for decoded_field in decoded_fields:
            candidate_name = decoded_field.partition('=')[0]
            if _is_sensitive_query_name(candidate_name):
                raise UsageError('URL query contains a prohibited credential or signature field')
    return maximum_fields


def _validate_nested_query_content(raw_query):
    maximum_fields = 0
    for decoded_query in _decoded_query_name_layers(raw_query):
        normalized_query = unicodedata.normalize('NFKC', decoded_query)
        maximum_fields = max(maximum_fields, len(re.split(r'[?&;]', normalized_query)))
        for match in re.finditer(r'(?:^|[?&;=])([^?&;=]+)(?==)', normalized_query):
            if _is_sensitive_query_name(match.group(1)):
                raise UsageError('URL query contains a prohibited credential or signature field')
    return maximum_fields


def validate_public_https_url(value, resolve_dns=True):
    _validate_plain_text('URL', value, MAX_QUERY_LENGTH)
    if value != value.strip():
        raise UsageError('URL must not contain leading or trailing whitespace')
    if '\\' in value:
        raise UsageError('URL must not contain backslashes')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise UsageError('URL is malformed') from None
    if parsed.scheme.lower() != 'https' or not parsed.netloc or not parsed.hostname:
        raise UsageError('URL must use https and include a public host')
    if parsed.username is not None or parsed.password is not None:
        raise UsageError('URL must not contain credentials')
    if parsed.fragment:
        raise UsageError('URL fragments are not allowed for webpage or Lens requests')
    if port not in {None, 443}:
        raise UsageError('URL must use the standard HTTPS port 443')
    try:
        parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=MAX_URL_QUERY_FIELDS)
    except ValueError:
        raise UsageError('URL query is malformed or has too many fields') from None
    raw_query_fields = re.split(r'[&;]', parsed.query) if parsed.query else []
    effective_field_count = sum(
        _validate_query_name(raw_field.partition('=')[0])
        for raw_field in raw_query_fields
    )
    if parsed.query:
        effective_field_count = max(effective_field_count, _validate_nested_query_content(parsed.query))
    if effective_field_count > MAX_URL_QUERY_FIELDS:
        raise UsageError('URL query is malformed or has too many fields')

    hostname = parsed.hostname.rstrip('.').lower()
    if '%' in hostname:
        raise UsageError('URL host must not contain an IPv6 zone identifier')
    try:
        literal = ipaddress.ip_address(hostname.split('%', 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        _validate_global_address(hostname)
        return value
    try:
        canonical_hostname = hostname.encode('idna').decode('ascii').rstrip('.').lower()
    except UnicodeError:
        raise UsageError('URL host is invalid') from None
    if (
        not canonical_hostname
        or canonical_hostname == 'localhost'
        or canonical_hostname.endswith(BLOCKED_HOST_SUFFIXES)
    ):
        raise UsageError('URL host must be public')
    if '.' not in canonical_hostname:
        raise UsageError('URL host must be a public fully-qualified domain name')
    if resolve_dns:
        try:
            addresses = socket.getaddrinfo(canonical_hostname, 443, type=socket.SOCK_STREAM)
        except socket.gaierror:
            raise UsageError('URL host could not be resolved') from None
        if not addresses:
            raise UsageError('URL host could not be resolved')
        for address in addresses:
            _validate_global_address(address[4][0])
    return value


def _looks_like_legacy_search(ns):
    if ns.mode is None or ns.mode.lower() in ENDPOINTS or ns.mode.lower() in LEGACY_SEARCH_ALIASES:
        return False
    if ns.query is None:
        return False
    numeric_slots = [ns.query, ns.num_pos]
    locale_slots = [ns.page_pos, ns.gl_pos, ns.hl_pos]

    def _is_int_like(value):
        if value is None:
            return True
        try:
            int(value)
            return True
        except (TypeError, ValueError):
            return False

    def _is_locale_like(value):
        return value is None or bool(LOCALE_PATTERN.fullmatch(str(value)))

    return all(_is_int_like(value) for value in numeric_slots) and all(_is_locale_like(value) for value in locale_slots)


def parse_args(argv):
    if not argv:
        raise UsageError(get_usage())
    ns = build_parser().parse_args(argv)
    if not ns.mode:
        raise UsageError(get_usage())

    endpoint = ENDPOINTS.get(ns.mode.lower())
    if endpoint is None:
        if not _looks_like_legacy_search(ns):
            raise UsageError('Unknown mode / endpoint')
        if ns.hl_pos is not None:
            raise UsageError('legacy search form accepts only: query num page gl hl')
        endpoint = 'search'
        query = ns.mode
        num = ns.num if ns.num is not None else _parse_positional_int('num', ns.query, 5)
        page = ns.page if ns.page is not None else _parse_positional_int('page', ns.num_pos, 1)
        gl = ns.gl if ns.gl is not None else (ns.page_pos or DEFAULT_GL)
        hl = ns.hl if ns.hl is not None else (ns.gl_pos or DEFAULT_HL)
    else:
        query = ns.query
        num = ns.num if ns.num is not None else _parse_positional_int('num', ns.num_pos, 5)
        page = ns.page if ns.page is not None else _parse_positional_int('page', ns.page_pos, 1)
        gl = ns.gl if ns.gl is not None else (ns.gl_pos or DEFAULT_GL)
        hl = ns.hl if ns.hl is not None else (ns.hl_pos or DEFAULT_HL)

    explicit_fields = {
        'num': ns.num is not None or ns.num_pos is not None,
        'page': ns.page is not None or ns.page_pos is not None,
        'gl': ns.gl is not None or ns.gl_pos is not None,
        'hl': ns.hl is not None or ns.hl_pos is not None,
    }

    pick = ns.pick if ns.pick is not None else 1
    limit = ns.limit if ns.limit is not None else 10
    _require_range('num', num, MAX_NUM)
    _require_range('page', page, MAX_PAGE)
    _require_range('pick', pick, MAX_PICK)
    _require_range('limit', limit, MAX_LIMIT)
    _validate_locale('gl', gl)
    _validate_locale('hl', hl)
    identifiers = {
        'place-id': _validate_plain_text('place-id', ns.place_id, MAX_IDENTIFIER_LENGTH) if ns.place_id else None,
        'cid': _validate_plain_text('cid', ns.cid, MAX_IDENTIFIER_LENGTH) if ns.cid else None,
        'fid': _validate_plain_text('fid', ns.fid, MAX_IDENTIFIER_LENGTH) if ns.fid else None,
    }
    supplied_identifiers = [name for name, value in identifiers.items() if value]

    if endpoint in {'overview', 'examples'}:
        if (
            query or any(explicit_fields.values()) or ns.pick is not None or ns.all_results
            or supplied_identifiers or ns.json_mode or ns.raw_mode or ns.compact
            or ns.save_path or ns.limit is not None
        ):
            raise UsageError(f'{endpoint} does not accept query, request, or output options')
        query = query or endpoint
    elif endpoint == 'reviews':
        if len(supplied_identifiers) != 1:
            raise UsageError('reviews endpoint requires exactly one of: --place-id, --cid, or --fid')
        if query:
            raise UsageError('reviews does not accept a positional query; use exactly one place identifier')
        query = query or next(value for value in identifiers.values() if value)
    elif endpoint in {'webpage', 'lens'}:
        if not query:
            raise UsageError(f'{endpoint} endpoint requires a URL')
        validate_public_https_url(query, resolve_dns=False)
    elif not query:
        raise UsageError(get_usage())

    _validate_plain_text('query', query, MAX_QUERY_LENGTH)
    if endpoint != 'reviews' and supplied_identifiers:
        raise UsageError('--place-id, --cid, and --fid are only valid with reviews')
    unsupported_explicit_fields = {
        'reviews': ('num', 'page'),
        'maps': ('num', 'gl'),
        'autocomplete': ('num', 'page'),
        'webpage': ('num', 'page', 'gl', 'hl'),
        'lens': ('num', 'page'),
    }.get(endpoint, ())
    for field_name in unsupported_explicit_fields:
        if explicit_fields[field_name]:
            raise UsageError(f'--{field_name} is not supported by the {endpoint} endpoint')
    if endpoint == 'maps-reviews' and ns.all_results and ns.pick is not None:
        raise UsageError('--pick cannot be combined with --all for maps-reviews')
    if endpoint == 'maps-reviews' and ns.all_results and num > MAX_MAPS_ALL_PLACES:
        raise UsageError(f'num must be at most {MAX_MAPS_ALL_PLACES} with maps-reviews --all')
    if ns.all_results and endpoint != 'maps-reviews':
        raise UsageError('--all is only valid with maps-reviews')
    if ns.pick is not None and endpoint != 'maps-reviews':
        raise UsageError('--pick is only valid with maps-reviews')
    if ns.raw_mode and ns.json_mode:
        raise UsageError('--json cannot be combined with --raw')

    output_mode = 'raw' if ns.raw_mode else 'json' if ns.json_mode else 'pretty'
    if output_mode != 'pretty' and ns.limit is not None:
        raise UsageError('--limit is only valid with pretty output')
    if endpoint == 'webpage' and ns.limit is not None:
        raise UsageError('--limit is not supported by the webpage endpoint')
    if ns.compact and output_mode == 'pretty':
        raise UsageError('--compact requires --json or --raw')
    if ns.save_path is not None and not ns.save_path.strip():
        raise UsageError('--save requires a non-empty file path')
    if ns.save_path is not None and output_mode == 'pretty':
        raise UsageError('--save requires --json or --raw')
    return {
        'endpoint': endpoint, 'query': query, 'num': num, 'page': page, 'gl': gl, 'hl': hl,
        'output_mode': output_mode, 'compact': ns.compact, 'save_path': ns.save_path,
        'place_id': identifiers['place-id'], 'cid': identifiers['cid'], 'fid': identifiers['fid'],
        'pick': pick, 'limit': limit, 'all_results': ns.all_results,
    }
