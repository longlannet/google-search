import sys
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from workflows import run_maps_reviews, run_maps_reviews_all


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
