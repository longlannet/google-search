# Endpoints Reference

Use this file when you need endpoint-specific behavior, special parameters, or payload expectations.

## Core search endpoints

- `web` / `search`
  - Standard Google web search
  - Payload uses `q`
- `images`
  - Google image search
  - Payload uses `q`
- `news`
  - Google news search
  - Payload uses `q`
- `videos`
  - Google video search
  - Payload uses `q`
- `shopping`
  - Google shopping/product search
  - Payload uses `q`
- `scholar`
  - Google Scholar search
  - Payload uses `q`
- `patents`
  - Google patents search
  - Payload uses `q`

## Local / map endpoints

- `places`
  - Local/place search
  - Payload uses `q`
- `maps`
  - Map/local search
  - Payload uses `q`
  - Returned place rows may include:
    - `placeId`
    - `cid`
    - `fid`
- `reviews`
  - Place reviews lookup
  - Requires one of:
    - `--place-id`
    - `--cid`
    - `--fid`
  - Plain text `q` alone is not sufficient
- `maps-reviews`
  - This is a workflow implemented by the skill, not a native Serper endpoint
  - Flow:
    1. Call `maps`
    2. Select a place result
    3. Use that place's `placeId/cid/fid` to call `reviews`
  - Supports:
    - `--pick <n>` to choose a single result
    - `--all` to fetch reviews for all returned places

## Suggestion / extraction endpoints

- `autocomplete` / `suggest`
  - Query suggestions / autocomplete
  - Payload uses `q`
- `webpage`
  - Webpage text extraction
  - Payload uses `url`
- `lens`
  - Google Lens-style reverse image lookup
  - Payload uses `url`

## Aliases

These CLI aliases are accepted by the parser:

- `web` -> `search`
- `image` -> `images`
- `video` -> `videos`
- `place` -> `places`
- `map` -> `maps`
- `review` -> `reviews`
- `suggest` -> `autocomplete`
- `patent` -> `patents`
- `page` -> `webpage`
- `cheatsheet` / `quickref` / `help` -> `overview`
- `map-reviews` -> `maps-reviews`

## Default locale behavior

Unless overridden by CLI flags, the current defaults are:

- `gl=cn`
- `hl=zh-cn`

That means result ranking and wording may skew toward Chinese-region results by default.

## Output modes

All endpoints support these output styles through the CLI:

- pretty output (default)
- `--json`
- `--raw`
- `--compact`
- `--save <file>`

Notes:

- pretty output is intended for humans and does not show API key suffixes by default
- if you need round-robin fallback diagnostics during debugging, set `SERPER_DEBUG_RR=1`

## Special cases

### `reviews`

Use this when you already know a place identifier.

Examples of accepted identifiers:
- placeId
- cid
- fid

### `maps-reviews`

Use this when you want reviews but only have a human query like:
- `coffee shanghai`
- `best ramen tokyo`
- `bookstore near bund`

The workflow will find places first, then resolve the required review identifiers automatically.

### `webpage`

- Expects a URL-style input
- A successful request may still return empty or weak text if extraction quality is poor for that page

### `lens`

- Expects a URL-style input
- Empty matches do not necessarily mean request failure; sometimes the lookup simply returns no useful candidate set
