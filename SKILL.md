---
name: google-search
description: Real-time Google search using Serper.dev API. Use for general web searches, news, images, videos, shopping, places, maps, reviews, autocomplete, patents, webpage extraction, lens lookup, or finding specific information.
homepage: https://serper.dev
metadata:
  openclaw:
    emoji: "🔎"
    requires:
      bins: ["python3"]
---

# Google Search Skill (Serper.dev)

Use this skill when you need Google search results through Serper.dev, including web, news, images, maps, reviews, autocomplete, webpage extraction, Lens-style lookup, patents, shopping, and scholar results.

Keep the skill lean: use the bundled scripts for execution, and read the reference files only when you need detailed endpoint behavior or copy-paste examples.

## Core workflow

1. Run `scripts/search.py` with the appropriate mode.
2. Use default pretty output for human-readable results.
3. Use `--json` or `--raw` for machine-readable output.
4. Use `scripts/selfcheck.py` when validating keys, endpoints, or workflow health.

## Supported endpoint groups

- Core search: `web` / `search`, `images`, `news`, `videos`, `shopping`, `scholar`, `patents`
- Local and map search: `places`, `maps`, `reviews`, `maps-reviews`
- Suggestion and extraction: `autocomplete`, `webpage`, `lens`
- Help surfaces: `overview`, `cheatsheet`, `quickref`, `help`, `examples`

## Important rules

- `reviews` requires one of `--place-id`, `--cid`, or `--fid`.
- `maps-reviews` is a skill workflow, not a native Serper endpoint.
- `maps-reviews` defaults to the first maps result, supports `--pick`, and supports batch mode via `--all`.
- `webpage` and `lens` require a URL-style query.
- `--json` and `--raw` are mutually exclusive.
- `maps-reviews --all` cannot be combined with `--pick`.

## References

Read these only when needed:

- `references/endpoints.md`
  - Read when you need endpoint-specific behavior, payload expectations, or special rules.
- `references/examples.md`
  - Read when you need concrete command examples, machine-readable examples, or self-check examples.

## Bundled scripts

- `scripts/search.py` — main CLI entrypoint
- `scripts/selfcheck.py` — endpoint and workflow health check

Use `config/serper.env` to provide one or more API keys.
