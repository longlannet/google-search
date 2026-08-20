import sys


BIDI_CONTROL_CODEPOINTS = {
    0x061C,
    0x200E,
    0x200F,
    0x202A,
    0x202B,
    0x202C,
    0x202D,
    0x202E,
    0x2066,
    0x2067,
    0x2068,
    0x2069,
}
UNICODE_LINE_SEPARATOR_CODEPOINTS = {0x2028, 0x2029}
MAX_EXTERNAL_STRING_CHARS = 65_536
MAX_EXTERNAL_COLLECTION_ITEMS = 1_000
MAX_EXTERNAL_DEPTH = 24
BROKEN_PIPE_EXIT_CODE = 1


def _escaped_codepoint(codepoint):
    if codepoint <= 0xFFFF:
        return f'\\u{codepoint:04x}'
    return f'\\U{codepoint:08x}'


def sanitize_external_text(value, max_chars=MAX_EXTERNAL_STRING_CHARS):
    """Make untrusted text inert in terminals and model-visible output."""
    if not isinstance(value, str):
        value = str(value)

    truncated = len(value) > max_chars
    value = value[:max_chars]
    output = []
    for char in value:
        codepoint = ord(char)
        if (
            codepoint in BIDI_CONTROL_CODEPOINTS
            or codepoint in UNICODE_LINE_SEPARATOR_CODEPOINTS
            or codepoint < 0x20
            or 0x7F <= codepoint <= 0x9F
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            output.append(_escaped_codepoint(codepoint))
        else:
            output.append(char)
    if truncated:
        output.append('[truncated]')
    return ''.join(output)


def sanitize_external_data(value, depth=0):
    """Recursively bound and sanitize data received from an external service."""
    if depth >= MAX_EXTERNAL_DEPTH:
        return '[maximum nesting depth reached]'
    if isinstance(value, str):
        return sanitize_external_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        output = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_EXTERNAL_COLLECTION_ITEMS:
                output['_truncated'] = True
                break
            safe_key = sanitize_external_text(key, max_chars=256)
            output[safe_key] = sanitize_external_data(item, depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        output = [
            sanitize_external_data(item, depth + 1)
            for item in value[:MAX_EXTERNAL_COLLECTION_ITEMS]
        ]
        if len(value) > MAX_EXTERNAL_COLLECTION_ITEMS:
            output.append('[truncated]')
        return output
    return sanitize_external_text(value)


def sanitize_print_text(value):
    if not isinstance(value, str):
        value = str(value)
    output = []
    for char in value:
        codepoint = ord(char)
        if (
            codepoint in BIDI_CONTROL_CODEPOINTS
            or codepoint in UNICODE_LINE_SEPARATOR_CODEPOINTS
            or codepoint == 0x1B
            or 0x7F <= codepoint <= 0x9F
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            output.append(_escaped_codepoint(codepoint))
        elif codepoint < 0x20 and char not in {'\n', '\t'}:
            output.append(_escaped_codepoint(codepoint))
        else:
            output.append(char)
    return ''.join(output)


def safe_print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    try:
        print(*(
            sanitize_print_text(value)
            if isinstance(value, str)
            else sanitize_external_text(value)
            for value in args
        ), **kwargs)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        raise SystemExit(BROKEN_PIPE_EXIT_CODE)
