import socket
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from args import UsageError, parse_args, validate_public_https_url


def test_parse_web_search_success():
    result = parse_args(['web', 'OpenAI'])
    assert result['endpoint'] == 'search'
    assert result['query'] == 'OpenAI'
    assert result['gl'] == 'cn'
    assert result['hl'] == 'zh-cn'


def test_parse_reviews_requires_identifier():
    with pytest.raises(UsageError, match='reviews endpoint requires exactly one of'):
        parse_args(['reviews'])


def test_parse_webpage_requires_url():
    with pytest.raises(UsageError, match='webpage endpoint requires a URL'):
        parse_args(['webpage'])


def test_parse_lens_requires_url():
    with pytest.raises(UsageError, match='lens endpoint requires a URL'):
        parse_args(['lens'])


def test_json_and_raw_cannot_be_combined():
    with pytest.raises(UsageError, match='--json cannot be combined with --raw'):
        parse_args(['web', 'OpenAI', '--json', '--raw'])


def test_maps_reviews_all_cannot_combine_pick():
    with pytest.raises(UsageError, match='--pick cannot be combined with --all'):
        parse_args(['maps-reviews', 'coffee shanghai', '--all', '--pick', '2'])


def test_maps_reviews_all_cannot_combine_explicit_default_pick():
    with pytest.raises(UsageError, match='--pick cannot be combined with --all'):
        parse_args(['maps-reviews', 'coffee shanghai', '--all', '--pick', '1'])


def test_num_must_be_positive():
    with pytest.raises(UsageError, match='num must be between 1 and 100'):
        parse_args(['web', 'OpenAI', '--num', '0'])


def test_page_must_be_positive():
    with pytest.raises(UsageError, match='page must be between 1 and 100'):
        parse_args(['web', 'OpenAI', '--page', '-1'])


def test_limit_must_be_positive():
    with pytest.raises(UsageError, match='limit must be between 1 and 100'):
        parse_args(['web', 'OpenAI', '--limit', '0'])


def test_pick_must_be_positive():
    with pytest.raises(UsageError, match='pick must be between 1 and 20'):
        parse_args(['maps-reviews', 'coffee shanghai', '--pick', '0'])


def test_unknown_endpoint_raises_error():
    with pytest.raises(UsageError, match='Unknown mode / endpoint') as captured:
        parse_args(['newss'])
    assert 'newss' not in str(captured.value)


def test_unknown_endpoint_without_legacy_shape_still_raises_error():
    with pytest.raises(UsageError, match='Unknown mode / endpoint'):
        parse_args(['openai'])


def test_invalid_num_type_does_not_reflect_the_value():
    with pytest.raises(UsageError, match='Invalid command-line arguments') as captured:
        parse_args(['web', 'OpenAI', '--num', 'abc'])
    assert 'abc' not in str(captured.value)


def test_alias_endpoints_are_normalized():
    assert parse_args(['image', 'cute cat'])['endpoint'] == 'images'
    assert parse_args(['video', 'OpenAI'])['endpoint'] == 'videos'
    assert parse_args(['map', 'coffee shanghai'])['endpoint'] == 'maps'
    assert parse_args(['review', '--place-id', 'ChIJ...'])['endpoint'] == 'reviews'
    assert parse_args(['suggest', 'openai'])['endpoint'] == 'autocomplete'


def test_reviews_accepts_cid_and_fid_identifiers():
    assert parse_args(['reviews', '--cid', '1234567890'])['cid'] == '1234567890'
    assert parse_args(['reviews', '--fid', '0x123456:0xabcdef'])['fid'] == '0x123456:0xabcdef'


def test_reviews_rejects_multiple_identifiers():
    with pytest.raises(UsageError, match='exactly one'):
        parse_args(['reviews', '--place-id', 'pid', '--cid', 'cid'])


@pytest.mark.parametrize('value', ['101', '1000000'])
def test_num_has_upper_bound(value):
    with pytest.raises(UsageError, match='between 1 and 100'):
        parse_args(['web', 'OpenAI', '--num', value])


def test_query_has_length_bound():
    with pytest.raises(UsageError, match='2048-character limit'):
        parse_args(['web', 'x' * 2049])


@pytest.mark.parametrize('argv', [
    ['web', '   '],
    ['reviews', '--place-id', '\t'],
    ['reviews', '--cid', '\N{NO-BREAK SPACE}'],
    ['reviews', '--fid', '\n'],
])
def test_queries_and_identifiers_reject_whitespace_only_values(argv):
    with pytest.raises(UsageError):
        parse_args(argv)


@pytest.mark.parametrize('url', [
    'http://example.com/x',
    'https://user:pass@example.com/x',
    'https://localhost/x',
    'https://0.0.0.0/x',
    'https://127.0.0.1/x',
    'https://10.0.0.1/x',
    'https://100.64.0.1/x',
    'https://169.254.169.254/latest/meta-data',
    'https://224.0.0.1/x',
    'https://239.255.255.250/x',
    'https://240.0.0.1/x',
    'https://255.255.255.255/x',
    'https://[::]/x',
    'https://[::1]/x',
    'https://[fc00::1]/x',
    'https://[fe80::1]/x',
    'https://[ff02::1]/x',
    'https://[ff0e::1]/x',
    'https://[fec0::1]/x',
    'https://[feff::1]/x',
    'https://[::ffff:0.0.0.0]/x',
    'https://[::ffff:10.0.0.1]/x',
    'https://[::ffff:127.0.0.1]/x',
    'https://[::ffff:169.254.1.1]/x',
    'https://[::ffff:224.0.0.1]/x',
    'https://[::ffff:240.0.0.1]/x',
    'https://example.com:8443/x',
])
def test_url_modes_reject_non_public_https_targets(url):
    with pytest.raises(UsageError):
        parse_args(['webpage', url])


def test_url_modes_reject_hostname_resolving_private(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.4', 443)),
    ])
    with pytest.raises(UsageError, match='public unicast IP'):
        validate_public_https_url('https://images.example.com/cat.jpg')


@pytest.mark.parametrize('address', [
    '0.0.0.0',
    '10.0.0.1',
    '100.64.0.1',
    '127.0.0.1',
    '169.254.1.1',
    '224.0.0.1',
    '239.255.255.250',
    '240.0.0.1',
    '255.255.255.255',
    '::',
    '::1',
    'fc00::1',
    'fe80::1',
    'ff02::1',
    'ff0e::1',
    'fec0::1',
    'feff::1',
    '::ffff:0.0.0.0',
    '::ffff:10.0.0.1',
    '::ffff:127.0.0.1',
    '::ffff:169.254.1.1',
    '::ffff:224.0.0.1',
    '::ffff:240.0.0.1',
])
def test_url_modes_reject_hostname_resolving_non_public_unicast(monkeypatch, address):
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (family, socket.SOCK_STREAM, 6, '', (address, 443)),
    ])

    with pytest.raises(UsageError, match='public unicast IP'):
        validate_public_https_url('https://download.example.com/file')


def test_url_modes_accept_public_https(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:4860:4860::8888', 443, 0, 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::ffff:8.8.4.4', 443, 0, 0)),
    ])
    assert validate_public_https_url('https://example.com/page') == 'https://example.com/page'


def test_parse_url_mode_defers_dns_to_the_bounded_client(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: pytest.fail('parse must not resolve DNS'))
    assert parse_args(['webpage', 'https://example.com/page'])['endpoint'] == 'webpage'


def test_url_validation_resolves_canonical_ascii_idna_hostname(monkeypatch):
    resolved_hosts = []

    def fake_getaddrinfo(hostname, *args, **kwargs):
        resolved_hosts.append(hostname)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)

    url = 'https://b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher.example/page'
    assert validate_public_https_url(url) == url
    assert resolved_hosts == ['xn--bcher-kva.example']


@pytest.mark.parametrize('addresses', [
    ('8.8.8.8', '127.0.0.1'),
    ('10.0.0.1', '8.8.8.8'),
    ('2001:4860:4860::8888', 'ff02::1'),
    ('fec0::1', '2001:4860:4860::8888'),
])
def test_url_modes_reject_any_non_public_address_in_dns_answer(monkeypatch, addresses):
    records = []
    for address in addresses:
        family = socket.AF_INET6 if ':' in address else socket.AF_INET
        sockaddr = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
        records.append((family, socket.SOCK_STREAM, 6, '', sockaddr))
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: records)

    with pytest.raises(UsageError, match='public unicast IP'):
        validate_public_https_url('https://mixed.example.com/file')


def test_url_validation_does_not_cache_a_public_dns_decision(monkeypatch):
    answers = iter([
        [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))],
        [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))],
    ])
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: next(answers))

    url = 'https://rebind.example.com/file'
    assert validate_public_https_url(url) == url
    with pytest.raises(UsageError, match='public unicast IP'):
        validate_public_https_url(url)


def test_url_modes_accept_public_ipv4_mapped_literal():
    result = parse_args(['webpage', 'https://[::ffff:8.8.8.8]/page'])
    assert result['endpoint'] == 'webpage'


def test_maps_reviews_all_num_is_bounded_to_ten():
    with pytest.raises(UsageError, match='at most 10'):
        parse_args(['maps-reviews', 'coffee', '--all', '--num', '11'])


@pytest.mark.parametrize(('endpoint', 'flag', 'value'), [
    ('reviews', '--num', '3'),
    ('reviews', '--page', '1'),
    ('maps', '--num', '3'),
    ('maps', '--gl', 'us'),
    ('lens', '--page', '1'),
    ('autocomplete', '--num', '3'),
    ('webpage', '--hl', 'en'),
])
def test_endpoint_specific_ignored_fields_are_rejected(monkeypatch, endpoint, flag, value):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
    ])
    if endpoint == 'reviews':
        argv = [endpoint, '--place-id', 'place-id', flag, value]
    elif endpoint in {'lens', 'webpage'}:
        argv = [endpoint, 'https://example.com/item', flag, value]
    else:
        argv = [endpoint, 'query', flag, value]
    with pytest.raises(UsageError, match='not supported'):
        parse_args(argv)


@pytest.mark.parametrize('url', [
    'https://example.com/file#private-fragment',
    'https://example.com/file?token=secret',
    'https://example.com/file?api_key=secret',
    'https://example.com/file?access-token=secret',
    'https://example.com/file?X-Amz-Signature=secret',
    'https://example.com/file?X-Goog-Credential=secret',
    'https://example.com/file?sig=secret&sv=2024',
    'https://example.com/file?session_id=secret',
    'https://example.com/file?password=secret',
    'https://example.com/file?safe=value;%74oken=secret',
    'https://8.8.8.8/file?id_token=secret',
    'https://8.8.8.8/file?refresh-token=secret',
    'https://8.8.8.8/file?auth.token=secret',
    'https://8.8.8.8/file?AWSAccessKeyId=secret',
    'https://8.8.8.8/file?access_key_id=secret',
    'https://8.8.8.8/file?oauth_token=secret',
    'https://8.8.8.8/file?csrf_token=secret',
    'https://8.8.8.8/file?client_secret=secret',
    'https://8.8.8.8/file?X%252dAmz%252dCredential=secret',
    'https://8.8.8.8/file?%2569d_token=secret',
    'https://8.8.8.8/file?%252569d_token=secret',
    'https://8.8.8.8/file?token%26safe=x',
    'https://8.8.8.8/file?token%3bsafe=x',
    'https://8.8.8.8/file?%2574oken%253dvalue%2526safe=x',
    'https://8.8.8.8/file?\N{FULLWIDTH LATIN SMALL LETTER T}\N{FULLWIDTH LATIN SMALL LETTER O}\N{FULLWIDTH LATIN SMALL LETTER K}\N{FULLWIDTH LATIN SMALL LETTER E}\N{FULLWIDTH LATIN SMALL LETTER N}=secret',
    'https://8.8.8.8/file?%EF%BD%94%EF%BD%8F%EF%BD%8B%EF%BD%85%EF%BD%8E=secret',
    'https://8.8.8.8/file?safe\N{FULLWIDTH AMPERSAND}\N{FULLWIDTH LATIN SMALL LETTER T}\N{FULLWIDTH LATIN SMALL LETTER O}\N{FULLWIDTH LATIN SMALL LETTER K}\N{FULLWIDTH LATIN SMALL LETTER E}\N{FULLWIDTH LATIN SMALL LETTER N}=secret',
    'https://8.8.8.8/file?redirect=https%3A%2F%2Fexample.com%2Fnext%3Ftoken%3Dsecret',
    'https://8.8.8.8/file?safe=token%253Dsecret',
    'https://8.8.8.8/file?next=https%3A%2F%2Fexample.com%2F%3FX-Amz-Signature%3Dsecret',
])
def test_url_modes_reject_fragments_and_sensitive_query_fields(url):
    with pytest.raises(UsageError) as captured:
        parse_args(['webpage', url])
    assert 'secret' not in str(captured.value)


@pytest.mark.parametrize('url', [
    ' https://8.8.8.8/file',
    'https://8.8.8.8/file ',
    'https://[2606:4700:4700::1111%25eth0]/file',
    'https://example.com\\@127.0.0.1/file',
    'https:\\example.com/file',
])
def test_url_modes_reject_surrounding_whitespace_and_zone_identifiers(url):
    with pytest.raises(UsageError):
        parse_args(['webpage', url])


@pytest.mark.parametrize('url', [
    'https://service\N{IDEOGRAPHIC FULL STOP}local/file',
    'https://service\N{FULLWIDTH FULL STOP}internal/file',
    'https://localhost\N{HALFWIDTH IDEOGRAPHIC FULL STOP}/file',
])
def test_url_modes_reject_reserved_hosts_after_idna_canonicalization(url):
    with pytest.raises(UsageError, match='host must be public'):
        validate_public_https_url(url, resolve_dns=False)


@pytest.mark.parametrize(
    ('separator', 'inside_field_name'),
    [
        ('&', False),
        (';', False),
        ('%26', True),
        ('%3b', True),
        ('\N{FULLWIDTH AMPERSAND}', True),
    ],
)
def test_url_modes_enforce_query_field_limit_across_separator_forms(separator, inside_field_name):
    if inside_field_name:
        query = separator.join(f'x{index}' for index in range(101)) + '=1'
    else:
        query = separator.join(f'x{index}=1' for index in range(101))
    with pytest.raises(UsageError, match='too many fields'):
        validate_public_https_url(f'https://8.8.8.8/file?{query}', resolve_dns=False)


def test_url_modes_accept_exactly_one_hundred_query_fields():
    query = ';'.join(f'x{index}=1' for index in range(100))
    url = f'https://8.8.8.8/file?{query}'
    assert validate_public_https_url(url, resolve_dns=False) == url


def test_query_and_url_reject_unpaired_surrogates():
    with pytest.raises(UsageError, match='prohibited control characters'):
        parse_args(['web', 'bad\ud800query'])
    with pytest.raises(UsageError, match='prohibited control characters'):
        parse_args(['webpage', 'https://8.8.8.8/\udfff'])


def test_save_requires_machine_output_mode():
    with pytest.raises(UsageError, match='--save requires --json or --raw'):
        parse_args(['web', 'OpenAI', '--save', '/tmp/result.json'])


def test_save_rejects_an_explicit_empty_path():
    with pytest.raises(UsageError, match='--save requires a non-empty file path'):
        parse_args(['web', 'OpenAI', '--json', '--save', ''])
    with pytest.raises(UsageError, match='--save requires a non-empty file path'):
        parse_args(['web', 'OpenAI', '--json', '--save', '   '])


def test_compact_requires_machine_output_mode():
    with pytest.raises(UsageError, match='--compact requires --json or --raw'):
        parse_args(['web', 'OpenAI', '--compact'])


def test_limit_is_rejected_when_it_would_be_ignored():
    with pytest.raises(UsageError, match='only valid with pretty output'):
        parse_args(['web', 'OpenAI', '--json', '--limit', '3'])
    with pytest.raises(UsageError, match='not supported by the webpage'):
        parse_args(['webpage', 'https://8.8.8.8/page', '--limit', '3'])


@pytest.mark.parametrize('argv', [
    ['overview', 'ignored'],
    ['overview', '--json'],
    ['examples', '--num', '3'],
])
def test_local_help_modes_reject_ignored_options(argv):
    with pytest.raises(UsageError, match='does not accept'):
        parse_args(argv)


def test_reviews_rejects_positional_query_that_would_not_be_sent():
    with pytest.raises(UsageError, match='does not accept a positional query'):
        parse_args(['reviews', 'ignored query', '--place-id', 'place-id'])


def test_legacy_search_form_still_works():
    result = parse_args(['OpenAI', '3', '1', 'us', 'en'])
    assert result['endpoint'] == 'search'
    assert result['query'] == 'OpenAI'
    assert result['num'] == 3
    assert result['page'] == 1
    assert result['gl'] == 'us'
    assert result['hl'] == 'en'


def test_partial_legacy_shape_does_not_get_accepted():
    with pytest.raises(UsageError, match='Unknown mode / endpoint'):
        parse_args(['OpenAI', 'abc'])


def test_legacy_search_rejects_sixth_tail_value():
    with pytest.raises(UsageError, match='accepts only'):
        parse_args(['OpenAI', '3', '1', 'us', 'en', 'ignored'])
