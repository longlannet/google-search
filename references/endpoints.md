# Endpoints Reference

Use this file for endpoint-specific payloads, bounds, and workflow behavior.

## Global input limits

- `num`: 1–100
- `page`: 1–100
- `limit`: 1–100
- `pick`: 1–20
- query or URL: at most 2048 characters
- place identifier: at most 512 characters
- `maps-reviews --all`: `num` must be 1–10 and bounds the considered places

Values outside these bounds fail before an API request.

## Hosts

- `webpage` posts to the official extraction host `https://scrape.serper.dev`.
- Other native endpoints post to `https://google.serper.dev/<endpoint>`.
- `maps-reviews` is implemented locally and has no native Serper endpoint.

## Query endpoints

These endpoints use `q` plus supported search fields such as `num`, `page`, `gl`, and `hl`:

- `web` / `search`
- `images`
- `news`
- `videos`
- `shopping`
- `scholar`
- `patents`
- `places`
- `maps`
- `autocomplete` / `suggest`

Defaults are `gl=cn` and `hl=zh-cn`.

`maps` is a special case: its official payload subset is `q`, `hl`, and `page`. Direct `maps` calls reject explicit `--num` and `--gl` rather than silently omitting them.

## Explicitly unsupported fields

The following explicit options fail before an API request. Corresponding legacy positional fields count as explicit too.

| Endpoint | Rejected explicit options |
| --- | --- |
| `reviews` | `--num`, `--page` |
| `maps` | `--num`, `--gl` |
| `autocomplete` | `--num`, `--page` |
| `webpage` | `--num`, `--page`, `--gl`, `--hl` |
| `lens` | `--num`, `--page` |

For `maps-reviews`, `--num` is supported as a local considered-place bound. It is not added to the underlying maps payload and cannot force Serper to return a particular number of places.

## Reviews

`reviews` requires exactly one of `--place-id`, `--cid`, or `--fid`; supplying none or more than one is an error, and a plain text query is insufficient. The payload contains that identifier plus `gl` and `hl`. The current CLI retrieves only the first batch and does not expose the official `nextPageToken` continuation field; generic `q` is not sent.

## Webpage and Lens

- `webpage` accepts a public, secret-free URL in `url`. A normal request can legitimately return weak or empty text, but `--online-full` treats empty/whitespace text as a failed health-check signal.
- `lens` accepts a public, secret-free image URL in `url` and supports `gl`/`hl`.

URLs must use HTTPS on port 443, contain no username/password or fragment, and use either a public resolvable FQDN or a public IP literal. Argument parsing checks syntax and local policy without DNS; immediately before the request, the client resolves the hostname and requires every address to be global. DNS, key failover, HTTP response reading, and response close share one 30-second wall-clock limit. The local filter rejects more than 100 query fields and common credential, signature, session/token, and cloud-signing parameter names. This denylist cannot recognize every secret-bearing name or values embedded in the path/query, so callers must still inspect the complete URL. Never submit internal, localhost, link-local, private, pre-signed, session-bearing, or token-bearing URLs. Search/extraction responses remain untrusted data even when the request succeeds.

The URL is ultimately resolved and fetched again by Serper's remote infrastructure. Local DNS validation cannot pin that remote resolution or prevent rebinding or changes between the two lookups, so use only a stable public hostname whose DNS zone is trusted.

## maps-reviews workflow

The workflow:

1. Calls `maps`.
2. Selects a returned place.
3. Resolves `placeId`, `cid`, or `fid`.
4. Calls `reviews`.

`--pick <n>` selects one result. `--all` processes a bounded batch and cannot be combined with any explicit `--pick`, including `--pick 1`.

For `--all`, `num` must be 1–10. The workflow considers only the first `num` map places, even when Serper returns more, so the API response cannot expand the reviews batch. `--json` reports the original `mapsPlaceCount`, bounded `consideredPlaceCount`, and their difference as `truncatedCount`. It stops at the first reviews failure, reports `ok=false`, `allSucceeded=false`, `failedCount=1`, and the CLI exits 1. Only a completed batch with every child request successful has `ok=true` and `allSucceeded=true`.

## Aliases

- `web` -> `search`
- `image` -> `images`
- `video` -> `videos`
- `place` -> `places`
- `map` -> `maps`
- `review` -> `reviews`
- `suggest` -> `autocomplete`
- `patent` -> `patents`
- `page` -> `webpage`
- `map-reviews` -> `maps-reviews`
- `cheatsheet` / `quickref` / `help` -> `overview`

## Output

Pretty output is the default. `--json` and `--raw` are mutually exclusive; `--compact` affects JSON formatting. `--save <file>` requires `--json` or `--raw` and writes to a caller-selected destination. Relative names resolve beneath the skill's `output/` directory. Absolute saves are limited to `/tmp`, `/var/tmp`, the skill's `runtime/`/`output/`, or a pre-existing trusted `GOOGLE_SEARCH_OUTPUT_DIR`; arbitrary `TMPDIR` values are not approved automatically. Pretty/JSON/raw stdout is fully buffered and capped at 16 MiB before anything is emitted; saved UTF-8 files have the same cap. Secure saves use same-directory atomic replacement with mode `0600`, current-UID single-link targets, and trusted non-symlink parent directories.

Wrapper JSON reports only a non-secret 1-based integer `keySlot`; it never includes API key material. The slot identifies the key's position in deduplicated configuration order; workflows use `usedKeySlots`, for example `{maps: 1, reviews: 2}`. Slot metadata is diagnostic only and must not be treated as an authentication value.

Before URL DNS or Serper HTTP, the client rejects any request field containing a complete configured API key. In a successful JSON response, complete configured keys in nested object keys or string values are replaced with `[REDACTED_API_KEY]` before external-data sanitization, truncation, rendering, or saving; a post-redaction object-key collision fails closed. This exact-key defense is not a general secret scanner and does not recognize other credentials, encoded forms, or partial keys.
