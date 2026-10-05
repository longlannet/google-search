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
- native endpoint `--json` success: `{ok, trust, endpoint, keySlot, request, response}` wrapper
- native endpoint `--sanitized-json` success: bounded and sanitized API response only, without an added `ok` field
- `--raw`: compatibility alias for `--sanitized-json`; it is not byte-for-byte raw
- `--compact`: compact JSON with either structured mode
- `--save PATH`: structured modes only, mode `0600`; relative paths are inside skill `output/`. Absolute paths must be within `/tmp`, `/var/tmp`, skill `runtime/` or `output/`, or a trusted absolute `GOOGLE_SEARCH_OUTPUT_DIR`.

`maps-reviews --json` uses its own workflow object: `ok`, `trust`, `query`, `maps`, `usedKeySlots` and place counts. Single-place results add `pick`, `selectedPlace`, and `reviews`; `--all` adds `results`, `allSucceeded`, `failedCount`, `attemptedCount`, and `skippedCount`. These are not native endpoint wrappers and do not include `endpoint`, `keySlot`, `request`, or `response`.

Single-place sanitized/raw workflow output contains `ok`, `maps`, `reviews`, and `error`. The `--all` variant contains `ok`, `allSucceeded`, `failedCount`, `maps`, `results`, and `error`. Both aggregate multiple API calls.

Failures before a workflow result exists use `{ok:false, trust, endpoint, error}` in `--json` or `{ok:false, error}` in sanitized/raw mode. Workflow failures can return partial results instead. Check the exit status before selecting a success schema; check `ok` when locally generated and `allSucceeded` for a completed `--all` result.

Every successful structured result is still untrusted external content.
