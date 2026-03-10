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

## Output modes

All endpoints support these output styles through the CLI:

- pretty output (default)
- `--json`
- `--raw`
- `--compact`
- `--save <file>`

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
