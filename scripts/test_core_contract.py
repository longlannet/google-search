import contextlib
import io
import json
import socket
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import requests

import args
import client
import io_common
import renderers_json
import search
import secure_io
import workflows


VALID_KEY_1 = 'serper-test-key-00000001'
VALID_KEY_2 = 'serper-test-key-00000002'


class FakeResponse:
    def __init__(self, status=200, payload=None, body=None, headers=None, chunks=None):
        self.status_code = status
        if body is None:
            body = json.dumps(payload if payload is not None else {}).encode('utf-8')
        self._chunks = chunks if chunks is not None else [body]
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self._chunks

    def close(self):
        self.closed = True


def run_search(argv):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = search.main(argv)
    return status, output.getvalue()


class ArgsContractTests(unittest.TestCase):
    def test_aliases_and_legacy_form(self):
        self.assertEqual(args.parse_args(['image', 'cat'])['endpoint'], 'images')
        parsed = args.parse_args(['OpenAI', '3', '2', 'us', 'en'])
        self.assertEqual(
            (parsed['endpoint'], parsed['num'], parsed['page'], parsed['gl'], parsed['hl']),
            ('search', 3, 2, 'us', 'en'),
        )

    def test_sanitized_json_is_native_raw_alias(self):
        parsed = args.parse_args(['web', 'OpenAI', '--sanitized-json', '--compact'])
        self.assertEqual(parsed['output_mode'], 'raw')
        self.assertTrue(parsed['compact'])
        with self.assertRaises(args.UsageError):
            args.parse_args(['web', 'OpenAI', '--json', '--sanitized-json'])

    def test_numeric_bounds(self):
        cases = [
            (['web', 'q', '--num', '0'], 'num'),
            (['web', 'q', '--page', '101'], 'page'),
            (['maps-reviews', 'q', '--pick', '21'], 'pick'),
            (['web', 'q', '--limit', '101'], 'limit'),
        ]
        for argv, field in cases:
            with self.subTest(argv=argv):
                with self.assertRaisesRegex(args.UsageError, field):
                    args.parse_args(argv)

    def test_reviews_requires_exactly_one_identifier(self):
        with self.assertRaisesRegex(args.UsageError, 'exactly one'):
            args.parse_args(['reviews'])
        with self.assertRaisesRegex(args.UsageError, 'exactly one'):
            args.parse_args(['reviews', '--place-id', 'one', '--cid', 'two'])
        self.assertEqual(args.parse_args(['reviews', '--fid', 'fid'])['fid'], 'fid')

    def test_url_policy_rejects_query_fragment_credentials_and_private_hosts(self):
        urls = [
            'https://example.com/path?x=1',
            'https://example.com/path?',
            'https://example.com/path#fragment',
            'https://user:pass@example.com/path',
            'https://127.0.0.1/path',
            'http://example.com/path',
        ]
        for endpoint in ('webpage', 'lens'):
            for url in urls:
                with self.subTest(endpoint=endpoint, url=url):
                    with self.assertRaises(args.UsageError):
                        args.parse_args([endpoint, url])

    def test_url_query_rejection_precedes_dns(self):
        with mock.patch.object(socket, 'getaddrinfo') as resolver:
            with self.assertRaisesRegex(args.UsageError, 'query strings'):
                args.validate_public_https_url('https://example.com/path?x=1')
        resolver.assert_not_called()

    def test_url_dns_rejects_any_non_public_answer(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443)),
        ]
        with mock.patch.object(socket, 'getaddrinfo', return_value=answers):
            with self.assertRaisesRegex(args.UsageError, 'public unicast'):
                args.validate_public_https_url('https://example.com/path')

    def test_maps_pagination_is_disabled(self):
        for argv in (
            ['maps', 'coffee', '--page', '2'],
            ['maps-reviews', 'coffee', '--page', '2'],
            ['maps-reviews', 'coffee', '5', '2'],
        ):
            with self.subTest(argv=argv):
                with self.assertRaisesRegex(args.UsageError, 'page > 1') as captured:
                    args.parse_args(argv)
                self.assertIn(captured.exception.endpoint, {'maps', 'maps-reviews'})
        with self.assertRaises(args.UsageError) as captured:
            args.parse_args(['maps', 'coffee', '5', '2'])
        self.assertEqual(captured.exception.endpoint, 'maps')
        self.assertEqual(args.parse_args(['maps', 'coffee'])['page'], 1)

    def test_scholar_rejects_explicit_num_only(self):
        self.assertEqual(args.parse_args(['scholar', 'paper'])['num'], 5)
        for argv in (
            ['scholar', 'paper', '--num', '5'],
            ['scholar', 'paper', '5'],
        ):
            with self.subTest(argv=argv):
                with self.assertRaisesRegex(args.UsageError, 'not supported') as captured:
                    args.parse_args(argv)
                self.assertEqual(captured.exception.endpoint, 'scholar')

    def test_output_option_contracts(self):
        cases = [
            ['web', 'q', '--compact'],
            ['web', 'q', '--save', '/tmp/result.json'],
            ['web', 'q', '--json', '--limit', '2'],
            ['maps-reviews', 'q', '--all', '--pick', '1'],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                with self.assertRaises(args.UsageError):
                    args.parse_args(argv)

    def test_endpoint_specific_options_are_rejected_with_endpoint_context(self):
        cases = [
            (['reviews', '--place-id', 'pid', '--num', '1'], 'reviews'),
            (['maps', 'coffee', '--gl', 'us'], 'maps'),
            (['autocomplete', 'open', '--page', '2'], 'autocomplete'),
            (['webpage', 'https://example.com/page', '--hl', 'en'], 'webpage'),
            (['lens', 'https://example.com/image', '--page', '2'], 'lens'),
        ]
        for argv, endpoint in cases:
            with self.subTest(argv=argv):
                with self.assertRaises(args.UsageError) as captured:
                    args.parse_args(argv)
                self.assertEqual(captured.exception.endpoint, endpoint)

    def test_controls_locales_and_workflow_only_flags_are_rejected(self):
        cases = [
            ['web', 'line\nbreak'],
            ['web', 'q', '--gl', 'en_US'],
            ['web', 'q', '--all'],
            ['web', 'q', '--pick', '2'],
            ['maps-reviews', 'q', '--all', '--num', '11'],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                with self.assertRaises(args.UsageError):
                    args.parse_args(argv)

    def test_public_url_resolution_accepts_only_public_answers(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        with mock.patch.object(socket, 'getaddrinfo', return_value=answers) as resolver:
            value = args.validate_public_https_url('https://example.com/public/path')
        self.assertEqual(value, 'https://example.com/public/path')
        resolver.assert_called_once_with('example.com', 443, type=socket.SOCK_STREAM)


class ClientContractTests(unittest.TestCase):
    def make_client(self, responses, keys=None, start=0):
        post = mock.Mock(side_effect=responses)
        patches = [
            mock.patch.object(client, '_session', mock.Mock(post=post)),
            mock.patch.object(client, 'load_api_keys', return_value=keys or [VALID_KEY_1]),
            mock.patch.object(client, 'get_next_key_index', return_value=start),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return post

    def test_official_payload_matrix(self):
        cases = [
            ('search', 'q', {}, {'q': 'q', 'num': 3, 'page': 1, 'gl': 'us', 'hl': 'en'}),
            ('maps', 'q', {}, {'q': 'q', 'hl': 'en', 'page': 1}),
            ('autocomplete', 'q', {}, {'q': 'q', 'gl': 'us', 'hl': 'en'}),
            ('scholar', 'q', {}, {'q': 'q', 'page': 1, 'gl': 'us', 'hl': 'en'}),
            ('reviews', 'q', {'place_id': 'pid'}, {'placeId': 'pid', 'gl': 'us', 'hl': 'en'}),
        ]
        for endpoint, query, kwargs, expected in cases:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    client._build_payload(endpoint, query, 3, 1, 'us', 'en', **kwargs),
                    expected,
                )

    def test_url_payloads_validate_without_query(self):
        with mock.patch.object(client, 'validate_public_https_url') as validate:
            webpage = client._build_payload('webpage', 'https://example.com/page', 3, 1, 'us', 'en')
            lens = client._build_payload('lens', 'https://example.com/image', 3, 1, 'us', 'en')
        self.assertEqual(webpage, {'url': 'https://example.com/page'})
        self.assertEqual(lens, {'url': 'https://example.com/image', 'gl': 'us', 'hl': 'en'})
        self.assertEqual(validate.call_count, 2)

    def test_direct_maps_pagination_is_rejected(self):
        with self.assertRaisesRegex(client.SerperAPIError, 'page > 1'):
            client._build_payload('maps', 'q', 3, 2, 'us', 'en')

    def test_only_auth_and_quota_statuses_fail_over(self):
        first = FakeResponse(status=401)
        second = FakeResponse(payload={'organic': [{'title': 'ok'}]})
        post = self.make_client([first, second], keys=[VALID_KEY_1, VALID_KEY_2])
        data, slot = client.do_request('search', 'OpenAI', 3)
        self.assertEqual(data['organic'][0]['title'], 'ok')
        self.assertEqual(slot, 2)
        self.assertEqual(post.call_count, 2)
        self.assertTrue(first.closed and second.closed)

    def test_server_error_stops_without_failover(self):
        first = FakeResponse(status=500)
        post = self.make_client([first, FakeResponse()], keys=[VALID_KEY_1, VALID_KEY_2])
        with self.assertRaisesRegex(client.SerperAPIError, 'HTTP 500'):
            client.do_request('search', 'OpenAI', 3)
        self.assertEqual(post.call_count, 1)

    def test_network_exception_is_fixed_and_does_not_fail_over(self):
        post = self.make_client(
            [requests.ConnectionError(f'detail {VALID_KEY_1}'), FakeResponse()],
            keys=[VALID_KEY_1, VALID_KEY_2],
        )
        with self.assertRaises(client.SerperAPIError) as captured:
            client.do_request('search', 'OpenAI', 3)
        self.assertNotIn(VALID_KEY_1, str(captured.exception))
        self.assertEqual(post.call_count, 1)

    def test_close_failure_preserves_primary_request_error(self):
        class BadClose(FakeResponse):
            def close(self):
                raise RuntimeError('close detail')

        self.make_client([BadClose(status=500)])
        with self.assertRaisesRegex(client.SerperAPIError, 'HTTP 500'):
            client.do_request('search', 'OpenAI', 3)

    def test_close_failure_without_primary_is_fixed(self):
        class BadClose(FakeResponse):
            def close(self):
                raise RuntimeError(f'close detail {VALID_KEY_1}')

        self.make_client([BadClose(payload={'organic': []})])
        with self.assertRaisesRegex(client.SerperAPIError, 'could not be closed safely') as captured:
            client.do_request('search', 'OpenAI', 3)
        self.assertNotIn(VALID_KEY_1, str(captured.exception))

    def test_response_size_and_json_shape_are_bounded(self):
        oversized = FakeResponse(headers={'Content-Length': str(client.MAX_RESPONSE_BYTES + 1)})
        self.make_client([oversized])
        with self.assertRaisesRegex(client.SerperAPIError, 'exceeds'):
            client.do_request('search', 'OpenAI', 3)

        bad = FakeResponse(body=b'[]')
        self.make_client([bad])
        with self.assertRaisesRegex(client.SerperAPIError, 'top-level'):
            client.do_request('search', 'OpenAI', 3)

    def test_response_redacts_all_configured_keys(self):
        response = FakeResponse(payload={
            f'key-{VALID_KEY_1}': f'value-{VALID_KEY_2}',
            'organic': [{'title': VALID_KEY_1}],
        })
        self.make_client([response], keys=[VALID_KEY_1, VALID_KEY_2])
        data, _ = client.do_request('search', 'OpenAI', 3)
        serialized = json.dumps(data, sort_keys=True)
        self.assertNotIn(VALID_KEY_1, serialized)
        self.assertNotIn(VALID_KEY_2, serialized)
        self.assertIn(client.REDACTED_API_KEY, serialized)

    def test_request_containing_configured_key_stops_before_http(self):
        post = self.make_client([])
        with self.assertRaisesRegex(client.SerperAPIError, 'must not contain'):
            client.do_request('search', f'prefix-{VALID_KEY_1}', 3)
        post.assert_not_called()

    def test_environment_keys_are_strict_deduplicated_and_preferred(self):
        environment = {'SERPER_API_KEYS': f'{VALID_KEY_1},{VALID_KEY_1}\n{VALID_KEY_2}'}
        with mock.patch.dict(client.os.environ, environment, clear=True):
            self.assertEqual(client.load_api_keys(), [VALID_KEY_1, VALID_KEY_2])
        with mock.patch.dict(client.os.environ, {'SERPER_API_KEY': ''}, clear=True):
            with self.assertRaises(client.SerperConfigError):
                client.load_api_keys()

    def test_protected_config_file_is_parsed_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
            config_dir = Path(temporary)
            key_file = config_dir / 'serper.env'
            key_file.write_text(f'{VALID_KEY_1}\nkey: {VALID_KEY_2}\n', encoding='ascii')
            key_file.chmod(0o600)
            with mock.patch.object(client, 'ENV_FILE', key_file), mock.patch.dict(
                client.os.environ, {}, clear=True,
            ):
                self.assertEqual(client.load_api_keys(), [VALID_KEY_1, VALID_KEY_2])
                key_file.unlink()
                key_file.symlink_to(config_dir / 'missing')
                with self.assertRaises(client.SerperConfigError):
                    client.load_api_keys()

    def test_round_robin_state_is_private_and_advances(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
            runtime_dir = Path(temporary) / 'runtime'
            state_file = runtime_dir / 'serper_rr.idx'
            with mock.patch.object(client, 'RUNTIME_DIR', runtime_dir), mock.patch.object(
                client, 'RR_INDEX_FILE', state_file,
            ):
                self.assertEqual(client.get_next_key_index(2), 0)
                self.assertEqual(client.get_next_key_index(2), 1)
            self.assertEqual(stat.S_IMODE(runtime_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(state_file.stat().st_mode), 0o600)

    def test_failover_exhaustion_reports_only_statuses(self):
        first = FakeResponse(status=429)
        second = FakeResponse(status=403)
        post = self.make_client([first, second], keys=[VALID_KEY_1, VALID_KEY_2])
        with self.assertRaisesRegex(client.SerperAPIError, '429,403') as captured:
            client.do_request('search', 'OpenAI', 3)
        self.assertNotIn(VALID_KEY_1, str(captured.exception))
        self.assertEqual(post.call_count, 2)
        self.assertTrue(first.closed and second.closed)

    def test_response_stream_errors_are_fixed_and_closed(self):
        class BrokenStream(FakeResponse):
            def iter_content(self, chunk_size):
                del chunk_size
                raise requests.exceptions.ChunkedEncodingError(f'private {VALID_KEY_1}')

        response = BrokenStream()
        self.make_client([response])
        with self.assertRaisesRegex(client.SerperAPIError, 'response stream failed') as captured:
            client.do_request('search', 'OpenAI', 3)
        self.assertNotIn(VALID_KEY_1, str(captured.exception))
        self.assertTrue(response.closed)

    def test_redaction_key_collisions_fail_closed(self):
        redacted_key = f'prefix-{client.REDACTED_API_KEY}'
        response = FakeResponse(payload={f'prefix-{VALID_KEY_1}': 1, redacted_key: 2})
        self.make_client([response])
        with self.assertRaisesRegex(client.SerperAPIError, 'ambiguous object keys'):
            client.do_request('search', 'OpenAI', 3)
        self.assertTrue(response.closed)

    def test_wall_deadline_and_invalid_numbers_have_stable_errors(self):
        self.make_client([client._RequestDeadlineExpired()])
        with self.assertRaisesRegex(client.SerperAPIError, 'wall-clock limit'):
            client.do_request('search', 'OpenAI', 3)

        invalid_number = FakeResponse(body=b'{"value": NaN}')
        self.make_client([invalid_number])
        with self.assertRaisesRegex(client.SerperAPIError, 'invalid JSON'):
            client.do_request('search', 'OpenAI', 3)

    def test_direct_payload_validation_rejects_bad_common_values_and_identifiers(self):
        cases = [
            ('search', 'q', True, 1, 'us', 'en', {}),
            ('search', 'q', 3, 1, 'bad_locale', 'en', {}),
            ('search', 'q', 3, 1, 'us', 'en', {'place_id': 'pid'}),
            ('reviews', 'q', 3, 1, 'us', 'en', {}),
        ]
        for endpoint, query, num, page, gl, hl, kwargs in cases:
            with self.subTest(endpoint=endpoint, kwargs=kwargs):
                with self.assertRaises(client.SerperAPIError):
                    client._build_payload(endpoint, query, num, page, gl, hl, **kwargs)


class OutputContractTests(unittest.TestCase):
    def test_external_text_and_mapping_keys_are_sanitized(self):
        text = io_common.sanitize_external_text('a\x1b\n\u202eb')
        self.assertNotIn('\x1b', text)
        self.assertNotIn('\n', text)
        self.assertNotIn('\u202e', text)
        value = {'a\u202eb': 1, 'a\\u202eb': 2}
        safe = io_common.sanitize_external_data(value)
        self.assertEqual(len(safe), 2)
        self.assertEqual(sorted(safe.values()), [1, 2])

    def test_truncation_marker_does_not_overwrite_external_value(self):
        original_limit = io_common.MAX_EXTERNAL_COLLECTION_ITEMS
        try:
            io_common.MAX_EXTERNAL_COLLECTION_ITEMS = 1
            safe = io_common.sanitize_external_data({'_truncated': 'external', 'other': 'value'})
        finally:
            io_common.MAX_EXTERNAL_COLLECTION_ITEMS = original_limit
        self.assertIn('external', safe.values())
        self.assertIn(True, safe.values())

    def test_json_output_cap_includes_stdout_newline(self):
        encoded = json.dumps({'v': '1234'}, ensure_ascii=False, separators=(',', ':'))
        with mock.patch.object(renderers_json, 'MAX_OUTPUT_BYTES', len(encoded.encode('utf-8'))):
            with self.assertRaises(secure_io.OutputSecurityError):
                renderers_json.serialize_json({'v': '1234'}, compact=True)

    def test_save_oserror_has_stable_classification(self):
        with mock.patch.object(renderers_json, 'secure_write_text', side_effect=OSError('disk detail')):
            with self.assertRaisesRegex(
                secure_io.OutputSecurityError, 'output could not be securely saved',
            ):
                renderers_json.save_output('{}', '/tmp/not-used')

    def test_secure_write_is_private_atomic_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
            root = Path(temporary)
            target = root / 'nested' / 'result.json'
            self.assertEqual(secure_io.secure_write_text('first', target), target)
            self.assertEqual(target.read_text(encoding='utf-8'), 'first')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

            victim = root / 'victim'
            victim.write_text('unchanged', encoding='utf-8')
            linked = root / 'linked'
            linked.symlink_to(victim)
            with self.assertRaises(secure_io.OutputSecurityError):
                secure_io.secure_write_text('attack', linked)
            self.assertEqual(victim.read_text(encoding='utf-8'), 'unchanged')

    def test_output_path_cannot_escape_allowed_roots(self):
        with self.assertRaises(secure_io.OutputSecurityError):
            secure_io.normalize_output_target('/etc/google-search-result.json')
        with self.assertRaises(secure_io.OutputSecurityError):
            secure_io.normalize_output_target('../runtime/result.json')

    def test_secure_writer_replaces_regular_files_and_rejects_symlink_parents(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
            root = Path(temporary)
            target = root / 'result.json'
            target.write_text('old', encoding='utf-8')
            secure_io.secure_write_text('new', target)
            self.assertEqual(target.read_text(encoding='utf-8'), 'new')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

            actual_parent = root / 'actual'
            actual_parent.mkdir()
            linked_parent = root / 'linked-parent'
            linked_parent.symlink_to(actual_parent, target_is_directory=True)
            with self.assertRaises(secure_io.OutputSecurityError):
                secure_io.secure_write_text('blocked', linked_parent / 'result.json')
            self.assertFalse((actual_parent / 'result.json').exists())


class SearchAndWorkflowContractTests(unittest.TestCase):
    def test_contract_errors_are_structured_and_make_no_request(self):
        cases = [
            (['webpage', 'https://example.com/?x=1', '--json', '--compact'], 'webpage'),
            (['maps', 'coffee', '--page', '2', '--json', '--compact'], 'maps'),
            (['scholar', 'paper', '--num', '5', '--json', '--compact'], 'scholar'),
        ]
        for argv, endpoint in cases:
            with self.subTest(endpoint=endpoint):
                with mock.patch.object(search, 'do_request') as request:
                    status, output = run_search(argv)
                self.assertEqual(status, 1)
                request.assert_not_called()
                payload = json.loads(output)
                self.assertFalse(payload['ok'])
                self.assertEqual(payload['endpoint'], endpoint)

    def test_alias_parse_error_remains_machine_readable(self):
        status, output = run_search(['invalid-mode', '--sanitized-json', '--compact'])
        self.assertEqual(status, 1)
        self.assertFalse(json.loads(output)['ok'])

    def test_request_summaries_match_actual_payloads(self):
        scholar = renderers_json._request_summary('scholar', 'paper', 99, 2, 'us', 'en')
        self.assertEqual(scholar, {'q': 'paper', 'page': 2, 'gl': 'us', 'hl': 'en'})
        self.assertNotIn('num', scholar)
        self.assertNotIn(
            'num', renderers_json._request_summary('maps', 'coffee', 99, 1, 'us', 'en')
        )

    def test_sanitized_json_executes_normal_search_path(self):
        with mock.patch.object(search, 'do_request', return_value=({'organic': []}, 2)) as request:
            status, output = run_search(['web', 'OpenAI', '--sanitized-json', '--compact'])
        self.assertEqual(status, 0)
        request.assert_called_once()
        self.assertEqual(json.loads(output), {'organic': []})

    def test_unexpected_error_does_not_leak_detail(self):
        with mock.patch.object(search, 'do_request', side_effect=RuntimeError('private detail')):
            status, output = run_search(['web', 'OpenAI', '--json', '--compact'])
        self.assertEqual(status, 1)
        self.assertNotIn('private detail', output)
        self.assertEqual(json.loads(output)['error'], 'Unexpected internal error (RuntimeError)')

    def test_maps_reviews_success_and_identifier_selection(self):
        maps = {'places': [{'title': 'Cafe', 'cid': 'cid-only'}]}
        reviews = {'reviews': [{'author': 'Alice'}]}
        with mock.patch.object(workflows, 'do_request', side_effect=[(maps, 1), (reviews, 2)]) as request:
            result = workflows.run_maps_reviews('coffee')
        self.assertTrue(result['ok'])
        self.assertEqual(request.call_args_list[1].kwargs['cid'], 'cid-only')
        self.assertEqual(result['usedKeySlots'], {'maps': 1, 'reviews': 2})

    def test_maps_reviews_all_stops_after_first_failure(self):
        maps = {'places': [
            {'title': 'A', 'placeId': 'a'},
            {'title': 'B', 'placeId': 'b'},
            {'title': 'C', 'placeId': 'c'},
        ]}
        with mock.patch.object(
            workflows, 'do_request',
            side_effect=[(maps, 1), ({'reviews': []}, 2), client.SerperAPIError('stop')],
        ) as request:
            result = workflows.run_maps_reviews_all('coffee', num=3)
        self.assertFalse(result['ok'])
        self.assertEqual(result['failedCount'], 1)
        self.assertEqual(result['attemptedCount'], 2)
        self.assertEqual(result['skippedCount'], 1)
        self.assertEqual(request.call_count, 3)

    def test_maps_reviews_all_is_bounded(self):
        with self.assertRaisesRegex(ValueError, 'between 1 and 10'):
            workflows.run_maps_reviews_all('coffee', num=11)

    def test_maps_reviews_handles_empty_out_of_range_and_missing_identifiers(self):
        cases = [
            ({'places': []}, 1, 'No places found'),
            ({'places': [{'title': 'A', 'placeId': 'a'}]}, 2, 'out of range'),
            ({'places': [{'title': 'A'}]}, 1, 'no supported review identifier'),
        ]
        for maps, pick, error in cases:
            with self.subTest(error=error), mock.patch.object(
                workflows, 'do_request', return_value=(maps, 1),
            ) as request:
                result = workflows.run_maps_reviews('coffee', pick=pick)
            self.assertFalse(result['ok'])
            self.assertIn(error, result['error'])
            request.assert_called_once()

    def test_maps_reviews_all_success_reports_every_attempt(self):
        maps = {'places': [
            {'title': 'A', 'placeId': 'a'},
            {'title': 'B', 'cid': 'b'},
        ]}
        with mock.patch.object(
            workflows, 'do_request',
            side_effect=[(maps, 1), ({'reviews': []}, 2), ({'reviews': []}, 1)],
        ) as request:
            result = workflows.run_maps_reviews_all('coffee', num=2)
        self.assertTrue(result['ok'])
        self.assertTrue(result['allSucceeded'])
        self.assertEqual(result['attemptedCount'], 2)
        self.assertEqual(result['usedKeySlots'], {'maps': 1, 'reviews': [2, 1]})
        self.assertEqual(request.call_count, 3)

    def test_api_errors_remain_machine_readable_without_private_detail(self):
        with mock.patch.object(
            search, 'do_request',
            side_effect=client.SerperAPIError('fixed public failure', kind='api'),
        ):
            status, output = run_search(['web', 'OpenAI', '--json', '--compact'])
        payload = json.loads(output)
        self.assertEqual(status, 1)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['endpoint'], 'search')
        self.assertEqual(payload['error'], 'fixed public failure')


class RepositoryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    def test_entrypoint_uses_core_search_module(self):
        run_script = (self.root / 'scripts' / 'run.sh').read_text(encoding='utf-8')
        self.assertIn('ENTRY="$SCRIPT_DIR/search.py"', run_script)
        self.assertNotIn('v21_entry', run_script)
        self.assertIn('exec "$PYTHON" -I -S -c', run_script)
        self.assertIn('sys.path[:0] = [entry.rsplit("/", 1)[0], site_packages]', run_script)

    def test_installer_has_lock_signal_and_target_safe_publish(self):
        installer = (self.root / 'scripts' / 'install.sh').read_text(encoding='utf-8')
        for required in (
            '/usr/bin/flock -x -n 9',
            "trap 'handle_signal 130' INT",
            '/usr/bin/mv -T -- "$CANDIDATE" "$VENV"',
            'check_python_runtime "$CANDIDATE/bin/python" "$CANDIDATE"',
            'check_python_runtime "$VENV/bin/python" "$VENV"',
            '/bin/kill -TERM "$ACTIVE_PID"',
            '/usr/bin/flock -u 9',
            'exec 9>&-',
        ):
            self.assertIn(required, installer)
        run_script = (self.root / 'scripts' / 'run.sh').read_text(encoding='utf-8')
        self.assertIn('/usr/bin/flock -s -n 9', run_script)

    def test_no_active_script_references_patch_layer(self):
        active = ('args.py', 'client.py', 'io_common.py', 'renderers_json.py', 'search.py', 'workflows.py')
        for name in active:
            text = (self.root / 'scripts' / name).read_text(encoding='utf-8')
            self.assertNotIn('_patch_core', text)
            self.assertNotIn('_google_search_v21_patched', text)

    def test_check_and_ci_use_one_offline_standard_library_gate(self):
        check_script = (self.root / 'scripts' / 'check.sh').read_text(encoding='utf-8')
        workflow = (self.root / '.github' / 'workflows' / 'test.yml').read_text(encoding='utf-8')
        self.assertIn('-I -S -B -c', check_script)
        self.assertNotIn('py_compile', check_script)
        self.assertNotIn('pytest', check_script)
        self.assertIn('scripts/check.sh --venv', workflow)
        self.assertNotIn('requirements-dev', workflow)
        self.assertNotIn('pytest', workflow)

    def test_current_docs_do_not_advertise_retired_runtime_modes(self):
        current_docs = '\n'.join(
            (self.root / path).read_text(encoding='utf-8')
            for path in ('README.md', 'SKILL.md', 'scripts/helptext.py')
        )
        for stale in (
            '--online-smoke', '--online-full', '--install-dev-dependencies',
            '原始 API JSON 输出', 'v21_entry.py',
        ):
            self.assertNotIn(stale, current_docs)


if __name__ == '__main__':
    unittest.main(verbosity=2)
