# Examples Reference

Use this file when you need copy-paste command examples.

## Basic search

```bash
python3 {baseDir}/scripts/search.py web "OpenAI"
python3 {baseDir}/scripts/search.py "OpenAI" 3 1 us en
python3 {baseDir}/scripts/search.py news "OpenAI" --limit 5
python3 {baseDir}/scripts/search.py images "cute cat" --json
python3 {baseDir}/scripts/search.py patents "OpenAI" --raw
```

## Local / maps / reviews

```bash
python3 {baseDir}/scripts/search.py maps "coffee shanghai"
python3 {baseDir}/scripts/search.py reviews --place-id ChIJ...
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
python3 {baseDir}/scripts/search.py maps-reviews "coffee shanghai" --json --compact
python3 {baseDir}/scripts/search.py maps-reviews "coffee shanghai" --all --raw --compact
```

## Overview / examples / self-check

```bash
python3 {baseDir}/scripts/search.py overview
python3 {baseDir}/scripts/search.py examples
python3 {baseDir}/scripts/selfcheck.py
python3 {baseDir}/scripts/selfcheck.py --full --compact
```

## Notes

- `--json` and `--raw` are mutually exclusive.
- `maps-reviews --all` cannot be combined with `--pick`.
- `reviews` requires `--place-id`, `--cid`, or `--fid`.
