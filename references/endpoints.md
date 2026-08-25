# Endpoint Contract

The supported command form is:

```text
scripts/run.sh ENDPOINT [QUERY] [OPTIONS]
```

Defaults: `num=5`, `page=1`, `gl=cn`, `hl=zh-cn`, `pick=1`, `limit=10`.

Global bounds: `num/page/limit <= 100`, `pick <= 20`, query/URL <= 2048 characters, identifiers <= 512 characters. All numeric values must be positive integers.

## Native endpoints

- `web` / `search`: `q`, `num`, `page`, `gl`, `hl`
- `images`: `q`, `num`, `page`, `gl`, `hl`
- `news`: `q`, `num`, `page`, `gl`, `hl`
- `videos`: `q`, `num`, `page`, `gl`, `hl`
- `places`: `q`, `num`, `page`, `gl`, `hl`
- `shopping`: `q`, `num`, `page`, `gl`, `hl`
- `patents`: `q`, `num`, `page`, `gl`, `hl`
- `scholar`: `q`, `page`, `gl`, `hl`; `num` is rejected and never sent
- `maps`: `q`, `page`, `hl`; `page > 1` is rejected until `ll` is supported
- `autocomplete`: `q`, `gl`, `hl`
- `reviews`: exactly one of `placeId`, `cid`, or `fid`, plus `gl`, `hl`
- `webpage`: public HTTPS URL without query, fragment, credentials, or non-public DNS
- `lens`: public HTTPS image URL under the same URL policy, plus `gl`, `hl`

Explicit unsupported options fail before any request:

- `reviews`: `num`, `page`
- `maps`: `num`, `gl`; `page` must remain 1
- `autocomplete`: `num`, `page`
- `scholar`: `num`
- `webpage`: `num`, `page`, `gl`, `hl`
- `lens`: `num`, `page`

## Local workflow

`maps-reviews` first calls Maps, selects a bounded result, then calls Reviews by the first available identifier. `--pick N` selects one result; `--all` processes at most 10 and cannot be combined with explicit `--pick`. Maps pagination above page 1 is rejected.

## Output

- default: bounded human-readable output
- `--json`: `{ok, trust, endpoint, keySlot, request, response}` wrapper
- `--sanitized-json`: bounded and sanitized API response only
- `--raw`: compatibility alias for `--sanitized-json`; it is not byte-for-byte raw
- `--compact`: compact JSON with either structured mode
- `--save PATH`: structured modes only; atomic safe writer policy remains in `secure_io.py`

Every successful structured result is still untrusted external content.
