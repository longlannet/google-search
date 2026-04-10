# Automation Notes

Use this file when integrating the skill into CI, cron jobs, wrappers, or other machine-driven flows.

## Install script

### Recommended machine-readable usage

```bash
bash scripts/install.sh --save-json /tmp/google-search-install.json --quiet
code=$?
```

### Exit codes

- `0` → `ok`
- `2` → `config_error`
- `3` → `dependency_error`
- `4` → `smoke_test_error`
- `5` → `selfcheck_error`
- `10` → `install_error`

### JSON fields

Common fields emitted by `--json` / `--save-json`:

- `ok`
- `status`
- `mode`
- `python`
- `runtimeSource`
- `smokeTest`
- `fullCheck`
- `venvCreated`
- `usedExistingVenv`
- `requirementsInstalled`
- `smokeTestResultPath`
- `selfcheckResultPath`
- `savedJsonPath`
- `exitKind`
- `exitCode`
- `error`

## Selfcheck script

### Recommended targeted usage

```bash
python3 scripts/selfcheck.py --group network --save /tmp/google-search-network.json --quiet
code=$?
```

### Exit codes

- `0` → `ok`
- `2` → `config_error`
- `3` → `network_error`
- `4` → `parsing_error`
- `5` → `workflow_error`
- `10` → `mixed_error`

### Useful patterns

#### Save only, no stdout

```bash
python3 scripts/selfcheck.py --group parsing --save /tmp/google-search-parsing.json --quiet
```

#### Stop on first failure

```bash
python3 scripts/selfcheck.py --group network --fail-fast --save /tmp/google-search-network.json
```

#### Full check for release validation

```bash
python3 scripts/selfcheck.py --full --save /tmp/google-search-full.json
```

## Practical guidance

- Prefer `--save-json` / `--save` when another process will consume results.
- Prefer `--quiet` in pipelines to avoid mixing structured output with chatty logs.
- Use `--group` instead of `--full` unless you really need broad coverage.
- Treat `mixed_error` as a signal to inspect the saved JSON rather than retry blindly.
