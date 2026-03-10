# openclaw-skill-google-search

A production-ready OpenClaw skill for Google search via [Serper.dev](https://serper.dev).

This skill turns Serper into a reusable OpenClaw / AgentSkills package with:

- multiple search endpoints
- human-friendly pretty output
- machine-friendly JSON / raw output
- local/maps → reviews workflow support
- examples and self-check commands
- references split out for easier agent consumption

## What this skill supports

### Search endpoints

- `web` / `search`
- `images`
- `news`
- `videos`
- `shopping`
- `scholar`
- `patents`
- `places`
- `maps`
- `reviews`
- `autocomplete`
- `webpage`
- `lens`

### Workflow helpers

- `maps-reviews`
  - first run `maps`
  - then automatically resolve `placeId` / `cid` / `fid`
  - then fetch `reviews`

### Help surfaces

- `overview`
- `cheatsheet`
- `quickref`
- `help`
- `examples`

---

## Repository layout

```text
google-search/
├── SKILL.md
├── README.md
├── requirements.txt
├── .gitignore
├── config/
│   └── serper.env.example
├── references/
│   ├── endpoints.md
│   └── examples.md
└── scripts/
    ├── args.py
    ├── client.py
    ├── renderers.py
    ├── search.py
    ├── selfcheck.py
    ├── utils.py
    └── workflows.py
```

---

## Prerequisites

- Python 3
- `requests` Python package
- A Serper API key

Install dependency:

```bash
pip install -r requirements.txt
```

---

## Configuration

Create your local config file from the example:

```bash
cp config/serper.env.example config/serper.env
```

Then edit `config/serper.env` and insert your real key(s):

```env
SERPER_API_KEY=your_real_key_here
```

The loader also supports multiple keys and round-robin rotation.

---

## Basic usage

### Standard web search

```bash
python3 scripts/search.py web "OpenAI"
```

### Legacy-compatible form

```bash
python3 scripts/search.py "OpenAI" 3 1 us en
```

### News search

```bash
python3 scripts/search.py news "OpenAI" --limit 5
```

### Image search

```bash
python3 scripts/search.py images "cute cat" --json
```

### Webpage extraction

```bash
python3 scripts/search.py webpage "https://openclaw.ai"
```

### Lens lookup

```bash
python3 scripts/search.py lens "https://example.com/image.jpg" --json --compact
```

---

## Maps and reviews

### Search maps

```bash
python3 scripts/search.py maps "coffee shanghai"
```

### Query reviews directly

Requires one of `--place-id`, `--cid`, or `--fid`:

```bash
python3 scripts/search.py reviews --place-id ChIJ...
```

### Workflow: maps → reviews

Pick a specific map result:

```bash
python3 scripts/search.py maps-reviews "coffee shanghai" --pick 2 --limit 3
```

Fetch reviews for all returned places:

```bash
python3 scripts/search.py maps-reviews "coffee shanghai" --all --limit 2
```

Notes:

- `maps-reviews --all` cannot be combined with `--pick`
- `reviews` does not work with a plain text query alone

---

## Machine-readable output

### Wrapper JSON

```bash
python3 scripts/search.py web "OpenAI" --json
```

### Raw API JSON

```bash
python3 scripts/search.py news "OpenAI" --raw
```

### Compact JSON

```bash
python3 scripts/search.py web "OpenAI" --json --compact
```

### Save to file

```bash
python3 scripts/search.py web "OpenAI" --json --save /tmp/serper.json
```

### `maps-reviews` output behavior

- `maps-reviews --json` returns a workflow wrapper
- `maps-reviews --raw` returns only raw chained payloads
- `maps-reviews --all --json` returns the batch wrapper
- `maps-reviews --all --raw` returns `{maps, results}`

---

## Self-check

Run a basic health check:

```bash
python3 scripts/selfcheck.py
```

Compact JSON output:

```bash
python3 scripts/selfcheck.py --compact
```

Full check:

```bash
python3 scripts/selfcheck.py --full
```

Basic self-check covers:

- `search`
- `images`
- `news`
- `autocomplete`
- `maps`
- `patents`
- `webpage`
- `lens`
- `maps-reviews`
- `maps-reviews-pick2`
- `maps-reviews-all`

---

## OpenClaw usage notes

This repository is structured as an OpenClaw-compatible skill:

- `SKILL.md` stays lean for triggering and navigation
- `references/` contains detailed endpoint and example docs
- `scripts/` contains stable execution logic

If you are using this inside an OpenClaw skills directory, keep your real API key in:

```text
config/serper.env
```

Do **not** commit that file.

---

## Security / repo hygiene

This repository intentionally ignores:

- `config/serper.env`
- `runtime/`
- `venv/`
- Python cache files

That keeps the repo safe to publish to a private GitHub repository without leaking your real API key.

---

## Recommended repository name

```text
openclaw-skill-google-search
```

---

## License / ownership

Use whatever license or private-repo policy fits your own OpenClaw workspace and custom skills setup.
