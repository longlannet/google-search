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
# Auto mode: reuse current python3 when it already meets runtime requirements;
# otherwise fall back to creating a local .venv.
bash scripts/install.sh
python3 scripts/search.py web "OpenAI"
python3 scripts/search.py news "OpenAI"
python3 scripts/smoke_test.py

# If you want an isolated environment explicitly:
bash scripts/install.sh --venv
.venv/bin/python scripts/search.py web "OpenAI"

# If you want machine-readable install output:
bash scripts/install.sh --json
bash scripts/install.sh --save-json /tmp/google-search-install.json
bash scripts/install.sh --save-json /tmp/google-search-install.json --quiet

# If you want grouped diagnostics explicitly:
python3 scripts/selfcheck.py --basic
python3 scripts/selfcheck.py --full
python3 scripts/selfcheck.py --group network
python3 scripts/selfcheck.py --group parsing
python3 scripts/selfcheck.py --group workflows
python3 scripts/selfcheck.py --group network,workflows --compact
python3 scripts/selfcheck.py --group workflows --save /tmp/google-search-workflows.json
python3 scripts/selfcheck.py --group network --fail-fast
python3 scripts/selfcheck.py --group parsing --save /tmp/google-search-parsing.json --quiet

# Or run install plus full diagnostics:
bash scripts/install.sh --full-check
```

## Workflow
1. Run `scripts/search.py` with the appropriate mode.
2. Use default pretty output for human-readable results.
3. Use `--json` or `--raw` for machine-readable output.
4. Use `scripts/smoke_test.py` for a fast minimal health check.
5. Use `scripts/selfcheck.py --basic` for a moderate grouped check.
6. Use `scripts/selfcheck.py --group network|parsing|workflows` for targeted diagnostics.
7. Use `scripts/selfcheck.py --save <file>` to persist machine-readable results.
8. Use `scripts/selfcheck.py --fail-fast` when you want it to stop at the first failing check.
9. Use `scripts/selfcheck.py --quiet` or `--no-stdout` when a pipeline only wants file output.
10. Inspect selfcheck exit codes when integrating with CI (`config_error=2`, `network_error=3`, `parsing_error=4`, `workflow_error=5`, `mixed_error=10`).
11. Use `scripts/selfcheck.py --full` only when validating keys, endpoints, or workflow health in depth.

## Notes
- `scripts/install.sh` supports `--system`, `--venv`, `--skip-smoke-test`, `--full-check`, `--json`, `--save-json <file>`, and `--quiet`.
- `install.sh` uses categorized exit codes to distinguish config, dependency, smoke-test, selfcheck, and generic install failures.
- `install.sh --json` returns additional machine-readable fields including runtime source, whether a venv was created or reused, result file paths, saved JSON path, and categorized exit info.
- `scripts/selfcheck.py` supports `--basic`, `--full`, `--group network|parsing|workflows`, `--save <file>`, `--fail-fast`, and `--quiet` / `--no-stdout` (groups can be comma-separated).
- `scripts/selfcheck.py` uses categorized exit codes to help CI distinguish config, network, parsing, workflow, and mixed failures.
- In auto mode, the installer reuses current `python3` when it already satisfies runtime requirements; otherwise it creates a local `.venv`.
- `reviews` requires one of `--place-id`, `--cid`, or `--fid`.
- `maps-reviews` is a workflow, not a native Serper endpoint.
- `webpage` and `lens` require a URL-style query.
- Read `references/endpoints.md`, `references/examples.md`, and `references/automation.md` only when needed.
- Use `config/serper.env` to provide one or more API keys.
