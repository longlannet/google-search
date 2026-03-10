import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from args import UsageError, parse_args


def test_parse_web_search_success():
    result = parse_args(['web', 'OpenAI'])
    assert result['endpoint'] == 'search'
    assert result['query'] == 'OpenAI'
    assert result['gl'] == 'cn'
    assert result['hl'] == 'zh-cn'


def test_parse_reviews_requires_identifier():
    with pytest.raises(UsageError, match='reviews endpoint requires one of'):
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


def test_num_must_be_positive():
    with pytest.raises(UsageError, match='num must be a positive integer'):
        parse_args(['web', 'OpenAI', '--num', '0'])


def test_page_must_be_positive():
    with pytest.raises(UsageError, match='page must be a positive integer'):
        parse_args(['web', 'OpenAI', '--page', '-1'])


def test_limit_must_be_positive():
    with pytest.raises(UsageError, match='limit must be a positive integer'):
        parse_args(['web', 'OpenAI', '--limit', '0'])


def test_pick_must_be_positive():
    with pytest.raises(UsageError, match='pick must be a positive integer'):
        parse_args(['maps-reviews', 'coffee shanghai', '--pick', '0'])


def test_unknown_endpoint_raises_error():
    with pytest.raises(UsageError, match='Unknown mode / endpoint: newss'):
        parse_args(['newss'])


def test_unknown_endpoint_without_legacy_shape_still_raises_error():
    with pytest.raises(UsageError, match='Unknown mode / endpoint: openai'):
        parse_args(['openai'])


def test_invalid_num_type_keeps_specific_argparse_message():
    with pytest.raises(UsageError, match="argument --num/-n: invalid int value: 'abc'"):
        parse_args(['web', 'OpenAI', '--num', 'abc'])


def test_alias_endpoints_are_normalized():
    assert parse_args(['image', 'cute cat'])['endpoint'] == 'images'
    assert parse_args(['video', 'OpenAI'])['endpoint'] == 'videos'
    assert parse_args(['map', 'coffee shanghai'])['endpoint'] == 'maps'
    assert parse_args(['review', '--place-id', 'ChIJ...'])['endpoint'] == 'reviews'
    assert parse_args(['suggest', 'openai'])['endpoint'] == 'autocomplete'


def test_reviews_accepts_cid_and_fid_identifiers():
    assert parse_args(['reviews', '--cid', '1234567890'])['cid'] == '1234567890'
    assert parse_args(['reviews', '--fid', '0x123456:0xabcdef'])['fid'] == '0x123456:0xabcdef'


def test_legacy_search_form_still_works():
    result = parse_args(['OpenAI', '3', '1', 'us', 'en'])
    assert result['endpoint'] == 'search'
    assert result['query'] == 'OpenAI'
    assert result['num'] == 3
    assert result['page'] == 1
    assert result['gl'] == 'us'
    assert result['hl'] == 'en'


def test_partial_legacy_shape_does_not_get_accepted():
    with pytest.raises(UsageError, match='Unknown mode / endpoint: OpenAI'):
        parse_args(['OpenAI', 'abc'])
