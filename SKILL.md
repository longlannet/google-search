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

# Google Search

Use this skill for real-time Google search through Serper.dev.

## When to use
Use this skill when the user wants:
- general web search
- news, images, videos, shopping, scholar, or patents
- places, maps, reviews, or maps-to-reviews workflows
- webpage extraction or Lens-style lookup

## Quick start
```bash
bash scripts/install.sh
.venv/bin/python scripts/search.py web "OpenAI"
.venv/bin/python scripts/search.py news "OpenAI"
.venv/bin/python scripts/selfcheck.py
```

## Workflow
1. Run `scripts/search.py` with the appropriate mode.
2. Use default pretty output for human-readable results.
3. Use `--json` or `--raw` for machine-readable output.
4. Use `scripts/selfcheck.py` when validating keys, endpoints, or workflow health.

## Notes
- `reviews` requires one of `--place-id`, `--cid`, or `--fid`.
- `maps-reviews` is a workflow, not a native Serper endpoint.
- `webpage` and `lens` require a URL-style query.
- Read `references/endpoints.md` and `references/examples.md` only when needed.
- Use `config/serper.env` to provide one or more API keys.
