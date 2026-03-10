import sys
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from workflows import render_maps_reviews_pretty, run_maps_reviews, run_maps_reviews_all
from client import _rr_debug_enabled


class DummyError(Exception):
    pass


def test_run_maps_reviews_success():
    maps_payload = {
        'places': [
            {
                'title': 'Cafe A',
                'address': 'Addr A',
                'placeId': 'pid-a',
                'cid': 'cid-a',
                'fid': 'fid-a',
            }
        ]
    }
    reviews_payload = {'reviews': [{'author': 'Alice', 'text': 'Nice'}]}

    with patch('workflows.do_request', side_effect=[(maps_payload, 'keymaps'), (reviews_payload, 'keyrevs')]):
        result = run_maps_reviews('coffee shanghai', pick=1)

    assert result['ok'] is True
    assert result['selectedPlace']['placeId'] == 'pid-a'
    assert result['usedKeySuffixes']['maps'] == 'maps'
    assert result['usedKeySuffixes']['reviews'] == 'revs'


def test_run_maps_reviews_pick_out_of_range():
    maps_payload = {'places': [{'title': 'Cafe A', 'placeId': 'pid-a'}]}

    with patch('workflows.do_request', return_value=(maps_payload, 'keymaps')):
        result = run_maps_reviews('coffee shanghai', pick=3)

    assert result['ok'] is False
    assert 'out of range' in result['error']


def test_run_maps_reviews_no_places_found():
    maps_payload = {'places': []}

    with patch('workflows.do_request', return_value=(maps_payload, 'keymaps')):
        result = run_maps_reviews('coffee shanghai', pick=1)

    assert result['ok'] is False
    assert result['selectedPlace'] is None
    assert 'No places found' in result['error']


def test_run_maps_reviews_all_no_places_found():
    maps_payload = {'places': []}

    with patch('workflows.do_request', return_value=(maps_payload, 'keymaps')):
        result = run_maps_reviews_all('coffee shanghai')

    assert result['ok'] is False
    assert result['allSucceeded'] is False
    assert result['failedCount'] == 0
    assert result['results'] == []
    assert 'No places found' in result['error']


def test_run_maps_reviews_all_partial_failure_has_structured_error_fields():
    maps_payload = {
        'places': [
            {'title': 'Cafe A', 'placeId': 'pid-a'},
            {'title': 'Cafe B', 'placeId': 'pid-b'},
        ]
    }
    reviews_payload = {'reviews': [{'author': 'Alice'}]}

    with patch(
        'workflows.do_request',
        side_effect=[
            (maps_payload, 'keymaps'),
            (reviews_payload, 'keyrevs'),
            DummyError('boom'),
        ],
    ):
        result = run_maps_reviews_all('coffee shanghai')

    assert result['ok'] is True
    assert result['allSucceeded'] is False
    assert result['failedCount'] == 1
    assert len(result['results']) == 2
    failed_entry = result['results'][1]
    assert failed_entry['ok'] is False
    assert failed_entry['errorType'] == 'DummyError'
    assert failed_entry['errorMessage'] == 'boom'
    assert 'DummyError: boom' == failed_entry['error']


def test_run_maps_reviews_all_all_success():
    maps_payload = {
        'places': [
            {'title': 'Cafe A', 'placeId': 'pid-a'},
            {'title': 'Cafe B', 'placeId': 'pid-b'},
        ]
    }
    reviews_payload_a = {'reviews': [{'author': 'Alice'}]}
    reviews_payload_b = {'reviews': [{'author': 'Bob'}]}

    with patch(
        'workflows.do_request',
        side_effect=[
            (maps_payload, 'keymaps'),
            (reviews_payload_a, 'keya1234'),
            (reviews_payload_b, 'keyb5678'),
        ],
    ):
        result = run_maps_reviews_all('coffee shanghai')

    assert result['ok'] is True
    assert result['allSucceeded'] is True
    assert result['failedCount'] == 0
    assert len(result['results']) == 2
    assert all(entry['ok'] is True for entry in result['results'])
    assert result['usedKeySuffixes']['maps'] == 'maps'
    assert result['usedKeySuffixes']['reviews'] == ['1234', '5678']


def test_run_maps_reviews_supports_cid_only_place_identifier():
    maps_payload = {
        'places': [
            {
                'title': 'Cafe CID',
                'cid': 'cid-only',
            }
        ]
    }
    reviews_payload = {'reviews': [{'author': 'Alice'}]}

    with patch('workflows.do_request', side_effect=[(maps_payload, 'keymaps'), (reviews_payload, 'keyrevs')]) as mocked:
        result = run_maps_reviews('coffee shanghai', pick=1)

    assert result['ok'] is True
    _, kwargs = mocked.call_args_list[1]
    assert kwargs['place_id'] is None
    assert kwargs['cid'] == 'cid-only'
    assert kwargs['fid'] is None
    assert result['selectedPlace']['cid'] == 'cid-only'


def test_run_maps_reviews_supports_fid_only_place_identifier():
    maps_payload = {
        'places': [
            {
                'title': 'Cafe FID',
                'fid': 'fid-only',
            }
        ]
    }
    reviews_payload = {'reviews': [{'author': 'Alice'}]}

    with patch('workflows.do_request', side_effect=[(maps_payload, 'keymaps'), (reviews_payload, 'keyrevs')]) as mocked:
        result = run_maps_reviews('coffee shanghai', pick=1)

    assert result['ok'] is True
    _, kwargs = mocked.call_args_list[1]
    assert kwargs['place_id'] is None
    assert kwargs['cid'] is None
    assert kwargs['fid'] == 'fid-only'
    assert result['selectedPlace']['fid'] == 'fid-only'


def test_render_maps_reviews_pretty_accepts_organic_reviews(capsys):
    result = {
        'ok': True,
        'query': 'coffee shanghai',
        'selectedPlace': {
            'title': 'Cafe Organic',
            'placeId': 'pid-organic',
        },
        'reviews': {
            'organic': [
                {'author': 'Alice', 'text': 'Nice place'}
            ]
        },
        'usedKeySuffixes': {'maps': 'maps', 'reviews': 'revs'},
    }

    render_maps_reviews_pretty(result, pick=1, gl='cn', hl='zh-cn', limit=5)
    output = capsys.readouterr().out
    assert 'Alice' in output
    assert 'Nice place' in output
    assert 'endpoint=maps-reviews' in output
    assert 'reviewsKey' not in output


def test_rr_debug_flag_parser(monkeypatch):
    monkeypatch.delenv('SERPER_DEBUG_RR', raising=False)
    assert _rr_debug_enabled() is False
    monkeypatch.setenv('SERPER_DEBUG_RR', '1')
    assert _rr_debug_enabled() is True
