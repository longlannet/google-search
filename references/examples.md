# Examples Reference

Use this file when you need copy-paste command examples.

`{baseDir}` means the root directory of this skill.

## Basic search

```bash
python3 {baseDir}/scripts/search.py web "OpenAI"
python3 {baseDir}/scripts/search.py "OpenAI" 3 1 us en
python3 {baseDir}/scripts/search.py news "OpenAI" --limit 5
python3 {baseDir}/scripts/search.py images "cute cat" --json
python3 {baseDir}/scripts/search.py patents "OpenAI" --raw

# If you explicitly want the isolated local venv path:
{baseDir}/.venv/bin/python {baseDir}/scripts/search.py web "OpenAI"
```

## Local / maps / reviews

```bash
python3 {baseDir}/scripts/search.py maps "coffee shanghai"
python3 {baseDir}/scripts/search.py reviews --place-id ChIJ...
python3 {baseDir}/scripts/search.py reviews --cid 1234567890
python3 {baseDir}/scripts/search.py reviews --fid 0x123456:0xabcdef
python3 {baseDir}/scripts/search.py maps-reviews "coffee shanghai" --pick 2 --limit 3
python3 {baseDir}/scripts/search.py maps-reviews "coffee shanghai" --all --limit 2
```

## Extraction / Lens

```bash
python3 {baseDir}/scripts/search.py webpage "https://openclaw.ai"
python3 {baseDir}/scripts/search.py lens "https://example.com/image.jpg" --json --compact
```

## Machine-readable output

```bash
python3 {baseDir}/scripts/search.py web "OpenAI" --json --save /tmp/serper.json
python3 {baseDir}/scripts/search.py news "OpenAI" --raw --compact
python3 {baseDir}/scripts/search.py reviews --place-id ChIJ... --raw --save /tmp/reviews.json
python3 {baseDir}/scripts/search.py maps-reviews "coffee shanghai" --json --compact
python3 {baseDir}/scripts/search.py maps-reviews "coffee shanghai" --all --raw --compact
```

## Install / overview / health-check

```bash
bash {baseDir}/scripts/install.sh
bash {baseDir}/scripts/install.sh --save-json /tmp/google-search-install.json --quiet
python3 {baseDir}/scripts/search.py overview
python3 {baseDir}/scripts/search.py examples
python3 {baseDir}/scripts/smoke_test.py
python3 {baseDir}/scripts/selfcheck.py --basic
python3 {baseDir}/scripts/selfcheck.py --group network --save /tmp/google-search-network.json --quiet
python3 {baseDir}/scripts/selfcheck.py --full --compact
```

## Notes

- Default install mode prefers current `python3` when runtime requirements are already satisfied; otherwise it falls back to local `.venv`.
- `--json` and `--raw` are mutually exclusive.
- `maps-reviews --all` cannot be combined with `--pick`.
- `reviews` requires `--place-id`, `--cid`, or `--fid`.
- pretty output does not show API key suffixes by default.
- set `SERPER_DEBUG_RR=1` if you need round-robin fallback diagnostics during debugging.
- For CI / automation conventions, also see `references/automation.md`.
