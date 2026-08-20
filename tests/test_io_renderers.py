import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BASE_DIR / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import io_common
from io_common import safe_print, sanitize_external_data, sanitize_external_text
import renderers_json
from renderers_json import serialize_json
from renderers_pretty import render_results
from secure_io import OutputSecurityError


def test_external_text_escapes_ansi_c0_c1_bidi_and_unicode_line_separators():
    raw = '\x1b[31mline\nnext\x85hidden\u202ereversed\u2028line\u2029paragraph'
    safe = sanitize_external_text(raw)
    assert '\x1b' not in safe
    assert '\n' not in safe
    assert '\x85' not in safe
    assert '\u202e' not in safe
    assert '\u2028' not in safe and '\u2029' not in safe
    assert r'\u001b' in safe
    assert r'\u000a' in safe
    assert r'\u0085' in safe
    assert r'\u202e' in safe
    assert r'\u2028' in safe and r'\u2029' in safe


def test_safe_print_stringifies_exceptions_before_sanitizing(capsys):
    safe_print(ValueError('bad\x1b[31m\u202evalue\u2028line\u2029paragraph\nforged\tcolumn\rreturn'))
    output = capsys.readouterr().out
    assert '\x1b' not in output and '\u202e' not in output and '\u2028' not in output and '\u2029' not in output
    assert '\t' not in output and '\r' not in output
    assert r'\u001b' in output and r'\u202e' in output
    assert r'\u2028' in output and r'\u2029' in output
    assert r'\u000a' in output and r'\u0009' in output and r'\u000d' in output


def test_surrogate_code_points_are_escaped_before_text_or_json_output(capsys):
    safe_print('left\ud800right\udfff')
    output = capsys.readouterr().out
    assert output == 'left\\ud800right\\udfff\n'
    encoded = serialize_json(sanitize_external_data({'value': '\ud800'}), compact=True)
    assert json.loads(encoded)['value'] == r'\ud800'


def test_safe_print_turns_broken_pipe_into_consistent_failure(monkeypatch):
    fake_stdout = Mock()
    monkeypatch.setattr(io_common.sys, 'stdout', fake_stdout)
    monkeypatch.setattr(io_common, 'print', Mock(side_effect=BrokenPipeError), raising=False)
    with pytest.raises(SystemExit) as captured:
        safe_print('payload')
    assert captured.value.code == io_common.BROKEN_PIPE_EXIT_CODE == 1
    fake_stdout.close.assert_called_once_with()


def test_recursive_sanitizer_bounds_depth_and_collection_size(monkeypatch):
    value = {'items': ['x'] * 1002}
    result = sanitize_external_data(value)
    assert len(result['items']) == 1001
    assert result['items'][-1] == '[truncated]'


def test_json_serialization_has_no_live_terminal_controls():
    payload = sanitize_external_data({'title': '\x1b[31mred\nline\u202e\u2028next\u2029last'})
    serialized = serialize_json(payload, compact=True)
    assert '\x1b' not in serialized and '\u202e' not in serialized
    assert '\u2028' not in serialized and '\u2029' not in serialized
    assert serialized.count('\n') == 0
    restored = json.loads(serialized)
    assert r'\u001b' in restored['title']
    assert r'\u2028' in restored['title'] and r'\u2029' in restored['title']


def test_json_serialization_enforces_stdout_size_limit(monkeypatch):
    monkeypatch.setattr(renderers_json, 'MAX_OUTPUT_BYTES', 32)
    try:
        serialize_json({'value': '\x00' * 20}, compact=True)
    except OutputSecurityError as error:
        assert 'exceeds' in str(error)
    else:
        raise AssertionError('expanded JSON output must be rejected')


def test_json_serialization_counts_the_stdout_newline(monkeypatch):
    payload = {'v': 'x' * 8}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    monkeypatch.setattr(renderers_json, 'MAX_OUTPUT_BYTES', len(encoded.encode('utf-8')))
    with pytest.raises(OutputSecurityError, match='exceeds'):
        serialize_json(payload, compact=True)


def test_pretty_renderer_tolerates_malformed_nested_shapes_and_escapes_controls(capsys):
    render_results('search', {
        'answerBox': ['unexpected'],
        'knowledgeGraph': 'unexpected',
        'organic': ['plain result', {'title': '\x1b[31mred\nforged', 'link': 42}],
        'news': {'not': 'a list'},
        'places': [7],
        'pagination': [],
    }, limit=5)
    output = capsys.readouterr().out
    assert 'plain result' in output
    assert '\x1b' not in output
    assert r'\u001b' in output and r'\u000a' in output
