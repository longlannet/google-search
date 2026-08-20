import argparse
import sys

from client import SerperAPIError, do_request, load_api_keys
from io_common import (
    safe_print,
    sanitize_external_data,
    sanitize_external_text,
    sanitize_print_text,
)
from renderers_json import serialize_json
from response_shapes import summarize_response_shape
from secure_io import OutputSecurityError


NOTES = [
    'Lightweight live check for the minimum search path.',
    'Only the search endpoint is tested to keep installation checks bounded.',
]
ARGUMENT_ERROR_EXIT_CODE = 2


class _ArgumentParseError(Exception):
    def __init__(self, message, usage):
        super().__init__(message)
        self.usage = usage


def _safe_argument_error(message):
    """Keep argparse diagnostics useful without reflecting attacker-controlled values."""
    if message.startswith('unrecognized arguments:'):
        return 'unrecognized arguments; use --help for supported options'
    explicit_value_marker = ': ignored explicit argument '
    if explicit_value_marker in message:
        return 'option does not accept a value'
    return 'invalid command-line arguments; use --help for supported options'


class _SafeArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            (file or sys.stderr).write(sanitize_print_text(message))

    def error(self, message):
        raise _ArgumentParseError(_safe_argument_error(message), self.format_usage())


def build_parser():
    parser = _SafeArgumentParser(
        prog='smoke_test.py', description='Run one bounded live Serper search check.', allow_abbrev=False,
    )
    parser.add_argument('--compact', action='store_true', help='Emit one-line JSON')
    return parser


def emit(payload, compact=False):
    payload = sanitize_external_data(payload)
    safe_print(serialize_json(payload, compact=compact))


def _compact_output_intent(argv):
    for token in argv:
        if token == '--':
            return False
        if token == '--compact' or token.startswith('--compact='):
            return True
    return False


def emit_argument_error(error, compact=False):
    emit({
        'ok': False,
        'trust': 'untrusted_external_content',
        'kind': 'smoke-test',
        'errorKind': 'usage_error',
        'error': sanitize_external_text(str(error), max_chars=1_000),
        'exitCode': ARGUMENT_ERROR_EXIT_CODE,
    }, compact=compact)


def emit_human_argument_error(error):
    sys.stderr.write(sanitize_print_text(error.usage))
    sys.stderr.write(f'smoke_test.py: error: {sanitize_print_text(str(error))}\n')


def main(argv=None):
    effective_argv = sys.argv[1:] if argv is None else argv
    try:
        options = build_parser().parse_args(effective_argv)
    except _ArgumentParseError as error:
        compact = _compact_output_intent(effective_argv)
        if compact:
            emit_argument_error(error, compact=True)
        else:
            emit_human_argument_error(error)
        raise SystemExit(ARGUMENT_ERROR_EXIT_CODE) from None
    query = 'OpenClaw'
    summary = {
        'ok': True,
        'trust': 'untrusted_external_content',
        'kind': 'smoke-test',
        'endpoint': 'search',
        'query': query,
        'keyCount': 0,
        'notes': NOTES,
    }
    try:
        keys = load_api_keys()
        summary['keyCount'] = len(keys)
        if not keys:
            raise SerperAPIError('No valid API keys found in the environment or protected config file', kind='config')
        data, key_slot = do_request('search', query, num=3, page=1, gl='us', hl='en')
        organic = data.get('organic') if isinstance(data.get('organic'), list) else []
        if not organic:
            summary['ok'] = False
            summary['error'] = 'search response does not include organic results'
        summary['keySlot'] = key_slot
        summary['shape'] = summarize_response_shape(data)
        summary['organicCount'] = len(organic)
    except SerperAPIError as error:
        summary['ok'] = False
        summary['error'] = f'SerperAPIError:{error.kind}: {error}'
    except Exception as error:
        summary['ok'] = False
        summary['error'] = f'Unexpected internal error ({type(error).__name__})'
    try:
        emit(summary, options.compact)
    except OutputSecurityError as error:
        emit({
            'ok': False,
            'kind': 'smoke-test',
            'error': str(error),
        }, options.compact)
        return 1
    return 0 if summary['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
