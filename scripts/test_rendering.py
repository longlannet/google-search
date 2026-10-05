import contextlib
import io
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import client
import io_common
import search


TEST_KEYS = ('serper-render-test-key-0001', 'serper-render-test-key-0002')


class WebpageRenderingTests(unittest.TestCase):
    def run_webpage(self, payload, *options):
        response = mock.Mock()
        response.status_code = 200
        response.headers = {}
        response.iter_content.return_value = [json.dumps(payload).encode('utf-8')]
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        output = io.StringIO()
        with (
            mock.patch.object(client, 'load_api_keys', return_value=list(TEST_KEYS)),
            mock.patch.object(client, 'get_next_key_index', return_value=0),
            mock.patch.object(socket, 'getaddrinfo', return_value=addresses),
            mock.patch.object(client._session, 'post', return_value=response) as post,
            contextlib.redirect_stdout(output),
        ):
            status = search.main(['webpage', 'https://example.com/article', *options])
        self.assertEqual(status, 0, output.getvalue())
        post.assert_called_once()
        response.close.assert_called_once()
        return output.getvalue()

    def test_decoded_paragraphs_supply_title_body_and_original_length(self):
        source = 'Example heading\n\nFirst paragraph.\n\nSecond paragraph.'
        output = self.run_webpage({'text': source})
        self.assertIn('   标题: Example heading\n', output)
        self.assertIn(f'   长度: {len(source)} 字符\n', output)
        self.assertIn('First paragraph.\n\nSecond paragraph.', output)
        self.assertEqual(output.count('Example heading'), 1)
        self.assertNotIn(r'\u000a', output)

    def test_explicit_title_preserves_the_first_body_paragraph(self):
        output = self.run_webpage({
            'title': 'Separate heading',
            'text': 'First paragraph.\n\nSecond paragraph.',
        })
        self.assertIn('   标题: Separate heading\n', output)
        self.assertIn('First paragraph.\n\nSecond paragraph.', output)

    def test_crlf_blank_lines_preserve_paragraphs_without_unsanitized_controls(self):
        source = 'Heading\r\n \t\r\nFirst line\r\nsecond line\r\n\r\nTail'
        output = self.run_webpage({'text': source})
        self.assertIn('   标题: Heading\n', output)
        self.assertIn(r'First line\u000asecond line' + '\n\nTail', output)
        self.assertIn(f'   长度: {len(source)} 字符\n', output)
        self.assertNotIn('\r', output)
        self.assertNotIn('\t', output)

    def test_literal_escapes_stay_literal_and_all_keys_are_redacted(self):
        heading = r'Literal \u000a\u000a heading'
        body = f'Body\x1b[31m\u202e {TEST_KEYS[0]}\t{TEST_KEYS[1]}'
        source = heading + '\n\n' + body + '\n\nTail'
        output = self.run_webpage({'text': source})
        self.assertIn(f'   标题: {heading}\n', output)
        self.assertIn(r'Body\u001b[31m\u202e [REDACTED_API_KEY]\u0009[REDACTED_API_KEY]', output)
        self.assertIn('\n\nTail', output)
        self.assertEqual(output.count(heading), 1)
        for key in TEST_KEYS:
            self.assertNotIn(key, output)
        for control in ('\x1b', '\u202e', '\t'):
            self.assertNotIn(control, output)

    def test_json_modes_preserve_the_existing_sanitized_field_contract(self):
        payload = {
            'text': f'Heading\n\nBody\x1b\u202e {TEST_KEYS[0]} literal \\u000a',
            'title': f'Title\n{TEST_KEYS[1]}',
            'metadata': {'text_paragraphs': 'external value'},
        }
        expected = io_common.sanitize_external_data(
            io_common.sanitize_external_data(client._redact_api_keys(payload, TEST_KEYS)),
        )
        for mode in ('--json', '--sanitized-json', '--raw'):
            with self.subTest(mode=mode):
                output = self.run_webpage(payload, mode, '--compact')
                result = json.loads(output)
                data = result['response'] if mode == '--json' else result
                self.assertEqual(data, expected)
                self.assertEqual(set(data), set(payload))
                self.assertNotIn('\n', data['text'])
                self.assertIn(r'\u000a\u000a', data['text'])
                for key in TEST_KEYS:
                    self.assertNotIn(key, output)

    def test_long_json_text_keeps_the_existing_repeated_sanitization_bound(self):
        payload = {'text': '\x1b' * io_common.MAX_EXTERNAL_STRING_CHARS}
        output = self.run_webpage(payload, '--sanitized-json', '--compact')
        expected = io_common.sanitize_external_data(io_common.sanitize_external_data(payload))
        self.assertEqual(json.loads(output), expected)

    def test_empty_body_and_summary_truncation_are_reported(self):
        empty = self.run_webpage({'text': '\r\n \t\r\n'})
        self.assertIn('未提取到网页正文', empty)
        output = self.run_webpage({'title': 'Heading', 'text': 'x' * 1000})
        self.assertIn('\n' + 'x' * 800 + '\n', output)
        self.assertNotIn('x' * 801, output)
        self.assertIn('已截断', output)


if __name__ == '__main__':
    unittest.main(verbosity=2)
