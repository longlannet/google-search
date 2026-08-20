import io
import sys
from contextlib import redirect_stdout

from args import UsageError, parse_args
from client import SerperAPIError, do_request
from helptext import print_examples, print_overview
from io_common import safe_print, sanitize_external_text, sanitize_print_text
from renderers_json import emit_json_wrapper, emit_raw_json, save_output, serialize_json
from renderers_pretty import render_results
from secure_io import MAX_OUTPUT_BYTES, OutputSecurityError, preflight_output_path
from workflows import (
    emit_maps_reviews_all_json,
    emit_maps_reviews_all_raw,
    emit_maps_reviews_json,
    emit_maps_reviews_raw,
    render_maps_reviews_all_pretty,
    render_maps_reviews_pretty,
    run_maps_reviews,
    run_maps_reviews_all,
)


def _emit_error(output_mode, endpoint, error_text, compact=False, save_path=None):
    error_text = sanitize_external_text(error_text, max_chars=1_000)
    if output_mode == 'json':
        payload = {
            'ok': False,
            'trust': 'untrusted_external_content',
            'endpoint': endpoint,
            'error': error_text,
        }
        text = serialize_json(payload, compact=compact)
    elif output_mode == 'raw':
        text = serialize_json({'ok': False, 'error': error_text}, compact=compact)
    else:
        safe_print(f'Request failed: {error_text}')
        return
    if save_path is not None:
        save_output(text, save_path)
    safe_print(text)


def _argument_output_intent(argv):
    """Find output flags only in the option-bearing portion before ``--``."""
    json_intent = False
    raw_intent = False
    compact_intent = False
    for token in argv:
        if token == '--':
            break
        if token == '--json' or token.startswith('--json='):
            json_intent = True
        elif token == '--raw' or token.startswith('--raw='):
            raw_intent = True
        elif token == '--compact' or token.startswith('--compact='):
            compact_intent = True
    output_mode = 'json' if json_intent else 'raw' if raw_intent else 'pretty'
    return output_mode, compact_intent


def _emit_pretty_buffered(render):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        render()
    text = sanitize_print_text(buffer.getvalue())
    if len(text.encode('utf-8')) > MAX_OUTPUT_BYTES:
        raise OutputSecurityError(f'pretty output exceeds the {MAX_OUTPUT_BYTES}-byte limit')
    safe_print(text, end='')


def _emit_workflow(result, all_results, output_mode, compact, save_path, pick, gl, hl, limit):
    if all_results:
        if output_mode == 'json':
            emit_maps_reviews_all_json(result, compact=compact, save_path=save_path)
        elif output_mode == 'raw':
            emit_maps_reviews_all_raw(result, compact=compact, save_path=save_path)
        else:
            _emit_pretty_buffered(
                lambda: render_maps_reviews_all_pretty(result, gl=gl, hl=hl, limit=limit)
            )
    else:
        if output_mode == 'json':
            emit_maps_reviews_json(result, compact=compact, save_path=save_path)
        elif output_mode == 'raw':
            emit_maps_reviews_raw(result, compact=compact, save_path=save_path)
        else:
            _emit_pretty_buffered(
                lambda: render_maps_reviews_pretty(result, pick=pick, gl=gl, hl=hl, limit=limit)
            )


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        parsed = parse_args(argv)
    except UsageError as error:
        machine_mode, compact_intent = _argument_output_intent(argv)
        error_line = str(error).splitlines()[0] if str(error).splitlines() else 'Invalid arguments'
        _emit_error(machine_mode, 'unknown', error_line, compact=compact_intent)
        return 1

    endpoint = parsed['endpoint']
    if endpoint == 'overview':
        try:
            _emit_pretty_buffered(print_overview)
            return 0
        except OutputSecurityError as error:
            _emit_error('pretty', endpoint, str(error))
            return 1
    if endpoint == 'examples':
        try:
            _emit_pretty_buffered(print_examples)
            return 0
        except OutputSecurityError as error:
            _emit_error('pretty', endpoint, str(error))
            return 1

    output_mode = parsed['output_mode']
    compact = parsed['compact']
    save_path = parsed['save_path']
    try:
        if save_path is not None:
            save_path = preflight_output_path(save_path)
        if endpoint == 'maps-reviews':
            if parsed['all_results']:
                result = run_maps_reviews_all(
                    parsed['query'], num=parsed['num'], page=parsed['page'],
                    gl=parsed['gl'], hl=parsed['hl'],
                )
            else:
                result = run_maps_reviews(
                    parsed['query'], num=parsed['num'], page=parsed['page'],
                    gl=parsed['gl'], hl=parsed['hl'], pick=parsed['pick'],
                )
            _emit_workflow(
                result, parsed['all_results'], output_mode, compact, save_path,
                parsed['pick'], parsed['gl'], parsed['hl'], parsed['limit'],
            )
            return 0 if result.get('ok') and (not parsed['all_results'] or result.get('allSucceeded')) else 1

        data, key_slot = do_request(
            endpoint, parsed['query'], parsed['num'], parsed['page'], parsed['gl'], parsed['hl'],
            place_id=parsed['place_id'], cid=parsed['cid'], fid=parsed['fid'],
        )
        if output_mode == 'raw':
            emit_raw_json(data, compact=compact, save_path=save_path)
        elif output_mode == 'json':
            emit_json_wrapper(
                endpoint, data, key_slot, parsed['gl'], parsed['hl'], parsed['query'],
                parsed['num'], parsed['page'], compact=compact, save_path=save_path,
                place_id=parsed['place_id'], cid=parsed['cid'], fid=parsed['fid'],
            )
        else:
            if endpoint == 'webpage':
                request_text = f"url={parsed['query']}"
            elif endpoint == 'lens':
                request_text = f"url={parsed['query']} | gl={parsed['gl']} hl={parsed['hl']}"
            elif endpoint == 'reviews':
                request_text = f"gl={parsed['gl']} hl={parsed['hl']}"
            elif endpoint == 'maps':
                request_text = f"q={parsed['query']} | page={parsed['page']} hl={parsed['hl']}"
            elif endpoint == 'autocomplete':
                request_text = f"q={parsed['query']} | gl={parsed['gl']} hl={parsed['hl']}"
            else:
                request_text = (
                    f"q={parsed['query']} | num={parsed['num']} page={parsed['page']} | "
                    f"gl={parsed['gl']} hl={parsed['hl']}"
                )

            def render_pretty():
                safe_print(f'Google (Serper) {endpoint}: {request_text}')
                render_results(endpoint, data, limit=parsed['limit'])
                safe_print(f'Data source: Google (via Serper.dev) | endpoint={endpoint}')

            _emit_pretty_buffered(render_pretty)
        return 0
    except OutputSecurityError as error:
        _emit_error(output_mode, endpoint, str(error), compact=compact, save_path=None)
        return 1
    except SerperAPIError as error:
        try:
            _emit_error(output_mode, endpoint, str(error), compact=compact, save_path=save_path)
        except OutputSecurityError as output_error:
            _emit_error(output_mode, endpoint, str(output_error), compact=compact, save_path=None)
        except OSError:
            _emit_error(
                output_mode, endpoint, 'output could not be securely saved',
                compact=compact, save_path=None,
            )
        return 1
    except Exception as error:
        _emit_error(
            output_mode, endpoint, f'Unexpected internal error ({type(error).__name__})',
            compact=compact, save_path=None,
        )
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
