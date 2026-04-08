# Examples Reference

Use this file when you need copy-paste command examples.

`{baseDir}` means the root directory of this skill.

## Basic search

```bash
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py web "OpenAI"
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py "OpenAI" 3 1 us en
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py news "OpenAI" --limit 5
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py images "cute cat" --json
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py patents "OpenAI" --raw
```

## Local / maps / reviews

```bash
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py maps "coffee shanghai"
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py reviews --place-id ChIJ...
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py reviews --cid 1234567890
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py reviews --fid 0x123456:0xabcdef
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py maps-reviews "coffee shanghai" --pick 2 --limit 3
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py maps-reviews "coffee shanghai" --all --limit 2
```

## Extraction / Lens

```bash
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py webpage "https://openclaw.ai"
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py lens "https://example.com/image.jpg" --json --compact
```

## Machine-readable output

```bash
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py web "OpenAI" --json --save /tmp/serper.json
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py news "OpenAI" --raw --compact
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py reviews --place-id ChIJ... --raw --save /tmp/reviews.json
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py maps-reviews "coffee shanghai" --json --compact
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py maps-reviews "coffee shanghai" --all --raw --compact
```

## Overview / examples / self-check

```bash
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py overview
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py examples
{baseDir}/.venv/bin/python {baseDir}/scripts/selfcheck.py
{baseDir}/.venv/bin/python {baseDir}/scripts/selfcheck.py --full --compact
```

## Notes

- `--json` and `--raw` are mutually exclusive.
- `maps-reviews --all` cannot be combined with `--pick`.
- `reviews` requires `--place-id`, `--cid`, or `--fid`.
- pretty output does not show API key suffixes by default.
- set `SERPER_DEBUG_RR=1` if you need round-robin fallback diagnostics during debugging.
