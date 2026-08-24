# Automation Notes

Use this file when integrating the skill into CI, cron, or another machine-driven flow. All default installation and project checks are offline; network/API checks require an explicit flag.

The automation host must place the checkout beneath a trusted physical directory chain. Every directory from the skill root through `/` must be current-UID/root-owned and non-group/world-writable; an exact `01777` current/root-owned sticky directory is the sole writable boundary, and its direct child must also be current/root-owned. Entry points snapshot and recheck ancestor device/inode metadata, so a foreign-owned `0755` workspace parent or a path replaced after runtime selection fails closed. Provision ownership outside these scripts; they never change a checkout parent's owner. Use `/bin/bash -p scripts/{run,install,check}.sh ...` or direct shebang execution, not plain `bash script`. The supported host also needs GNU `find`/`stat`/`readlink`/`rm`, procfs, and POSIX permissions. Dependency-install modes additionally require Python venv/ensurepip, Linux sealed memfd support, `/proc/self/fd`, `/proc/self/mountinfo`, and libc/kernel `renameat2`.

These filesystem checks do not isolate mutually hostile processes running under the same UID. Such a process may inspect credentials or process state and race or replace current-UID-owned resources outside the entry points' checked intervals. Run unattended or release automation under a dedicated UID, container, or VM where no untrusted same-UID process is active.

## Runtime selection

Use the wrapper for searches so installation and execution select the same verified Python:

```bash
/bin/bash -p scripts/run.sh --runtime-info
/bin/bash -p scripts/run.sh web "OpenAI" --json --compact
status=$?
```

`--runtime-info` emits one `google-search-runtime-info-v1|mode|displayPath|bindingToken` record. The path is diagnostic-only: never execute it or use it to construct a Python command. Installer/check automation consumes the binding token only for fixed runner tasks and revalidates the source/runtime snapshot on every invocation.

Search exit status is `0` only for success. Search usage, output-save, API, and workflow failures return `1`. The wrapper itself returns `2` for its own option errors and `3` when no healthy runtime is available. For `maps-reviews --all`, any failed child request makes the whole command fail; do not infer success from partial data.

## Offline installer

```bash
/bin/bash -p scripts/install.sh --save-json ./output/install-result.json --quiet
status=$?
```

The default command only selects an already compatible runtime. It requires exact versions and usable APIs for all five distributions in `requirements.txt`; it does not re-hash files in a reused system/local environment. System selection and execution use `-I -S`: the guard validates the physical system site roots (including Debian `dist-packages`), startup-hook metadata, every manifested file within those roots and each package tree for the five locked distributions, versions, and import origins before manually activating the roots. Manifested console scripts outside the site roots are neither read nor trusted. `.pth`, `sitecustomize`, and `usercustomize` are never executed; unsafe writable or symlinked hook/package trees fail closed. A persistent finder rejects every system-site module or namespace not claimed by the five validated manifests before its code executes, including optional modules that Requests or urllib3 may probe such as `chardet`, `simplejson`, `brotli`, `brotlicffi`, `backports.zstd`, or PySocks' `socks`. Unrelated extra distributions may be installed but cannot be loaded through the guarded runtime. Offline probes clear both Serper key variables. The command does not contact Serper or PyPI and does not create a venv. If no compatible runtime exists, it returns `dependency_error` (3).

Dependency installation is an explicit network operation:

```bash
/bin/bash -p scripts/install.sh --install-dependencies --save-json ./output/install-result.json --quiet
```

This obtains the persistent `.venv.install.lock` and creates a pip-free candidate under `.venv-build.*`. The unpinned ensurepip toolchain stays in a disposable `.venv-bootstrap.*`. After one verified read, the named requirements file is copied to a write/grow/shrink-sealed memfd. The candidate Python directly executes the bootstrap pip source and installs only binary artifacts from that immutable `/proc/self/fd` input; the pip process group is terminated if the 300-second bound expires. Before publication, the candidate distribution set must exactly equal the marker-applicable lock entries, and `.pth`, `sitecustomize.py`, `usercustomize.py`, pip, setuptools, or any other extra distribution is rejected. The installer then validates `pip check`, all runtime versions/APIs, and the candidate tree before publishing atomically with Linux `renameat2`.

Failures before the atomic rename leave the prior `.venv` unchanged. After an exchange, live validation and lock/source rechecks run while the original tree remains available at the staging name; failures are rolled back atomically. If acknowledgement or state is ambiguous, cleanup preserves the possible recovery tree instead of deleting it, and normal error output records a `postCommitWarning`. The option cannot be combined with `--system` and never invokes a system package manager.

For the complete offline project gate, explicitly install the development lock instead:

```bash
/bin/bash -p scripts/install.sh --install-dev-dependencies
/bin/bash -p scripts/check.sh --venv
```

This applies the same candidate transaction to `requirements-dev.txt`, including every marker-applicable runtime, pytest, and transitive development distribution. The persistent lock is current-UID, single-link, mode `0600`, and deliberately remains after installation; candidate/bootstrap directories and the lock are ignored by Git. `--install-dependencies` and `--install-dev-dependencies` are mutually exclusive, and neither can be combined with `--system`.

Serper checks are also opt-in and may consume quota:

```bash
/bin/bash -p scripts/install.sh --smoke-test
/bin/bash -p scripts/install.sh --full-check
```

`--full-check` invokes the actual full selfcheck, not the default/basic selection.

Installer/check command logs and live-check scratch results ignore `TMPDIR` and use randomized mode-`0600` names only after validating the canonical `/tmp` directory as root-owned mode `01777`. Logs and check scratch files are removed on exit. Installer live-result paths reported to the caller can remain under `/tmp/google-search-*`; remove them after consumption.

### Installer exit codes

- `0`: `ok`
- `2`: `config_error`
- `3`: `dependency_error`
- `4`: `smoke_test_error`
- `5`: `selfcheck_error`
- `10`: `install_error`

Machine-readable output includes the selected mode/Python, runtime source, whether dependency installation or API checks were requested, result paths, and categorized exit information. Treat the process exit status as authoritative even when a JSON document was saved.

`--install-dependencies` and `--install-dev-dependencies` are the only dependency-download modes. `--json`, `--save-json <file>`, and `--quiet` control automation output. The legacy `--skip-smoke-test` is a deprecated no-op because installation is already offline by default.

## Offline project check

```bash
/bin/bash -p scripts/check.sh
```

This is the default CI-equivalent local gate: full pytest, Python AST parsing, Bash syntax, ShellCheck when available, and the offline parsing selfcheck. The selected runtime must match the exact version of every marker-applicable distribution in `requirements-dev.txt`; reused environments may contain additional distributions. This metadata version check does not re-hash installed files or prove that a reused environment came from the lock's hashed artifacts. Use the explicit development-lock installation path when that artifact provenance is required. The pytest phase clears an ambient bytecode-cache prefix and forces `PYTHONDONTWRITEBYTECODE=1` for pytest and ordinary child interpreters; every isolated Python subprocess in the suite also passes `-B`, because `-I` ignores `PYTHON*` environment variables. Together these controls prevent imports from invalidating the bound runtime snapshot. The offline gate does not require `SERPER_API_KEY` and must not contact Serper. A nonzero exit means at least one gate failed.

`check.sh --online-smoke` adds the minimal live API check; `check.sh --online-full` adds `selfcheck.py --full`. Both are explicit, potentially billable operations. `--system`/`--venv` select the runtime, `--quiet` suppresses progress, and the legacy `RUN_SMOKE` environment variable is ignored.

CI materializes each Python 3.10–3.14 matrix interpreter as a pip-free repository-local `.venv`. Before the pinned `setup-python` action can execute a matrix Python, the job requires exactly one matching preinstalled patch runtime below `/opt/hostedtoolcache/Python`, removes group/other write permission from that version tree and its ancestors on the disposable runner, and validates the cache marker, canonical path, ownership, types, link count, and executable target. The action must then select that exact path; a missing or ambiguous preinstalled runtime fails closed instead of downloading or relocating a Python whose compiled prefix/RUNPATH would not match the project's loader-environment cleanup. A job-private bootstrap pip installs binary-only `requirements-dev.txt` artifacts into the target venv with `--python`, `--require-hashes`, and `--only-binary=:all:`; CI then proves the target contains neither pip/setuptools nor `.pth` startup hooks before running the complete `check.sh --venv` gate with third-party pytest plugin autoload disabled. It separately downloads the upstream Linux x86_64 ShellCheck v0.11.0 release asset, verifies both the archive and extracted-binary SHA-256 values, and installs it in a job-private directory. The complete gate receives that canonical absolute path through `--shellcheck`, revalidates its ownership, mode, link count, parent chain, exact version, and binary SHA-256 before and after lint, and fails closed instead of falling back to the rolling host ShellCheck. Without `--shellcheck`, local checks retain the optional trusted `/usr/bin/shellcheck` behavior. The checkout/setup-python action code is pinned to reviewed full commit SHAs; the GitHub-hosted `ubuntu-24.04` image, Python patch releases, and bundled pip remain rolling infrastructure rather than immutable inputs. Network selfchecks do not belong in pull-request CI.

## Selfcheck

For normal online diagnostics, prefer the isolated project-check launchers:

```bash
/bin/bash -p scripts/check.sh --online-smoke
/bin/bash -p scripts/check.sh --online-full
```

The default offline `check.sh` includes the complete parsing-only selfcheck. `--online-smoke` runs the fixed minimal Serper role, while `--online-full` runs all network, parsing, and workflow groups and validates the resulting machine document before reporting success. Direct interpreter paths, `scripts/runtime_guard.py`, and `scripts/selfcheck.py` are implementation details rather than supported automation entry points. A new automation mode must be added as a fixed runner task with an explicit argument and result protocol; it must not be assembled by executing the diagnostic path from `--runtime-info`.

### Selfcheck exit codes

- `0`: `ok`
- `2`: `config_error`
- `3`: `network_error`
- `4`: `parsing_error`
- `5`: `workflow_error`
- `10`: `mixed_error`

Unknown selfcheck flags are usage errors rather than silently selecting the default group. Argument errors exit with status `2` before configuration is loaded or the network is used. With an explicit `--json` or `--compact` before `--`, selfcheck emits exactly one safe JSON error document on stdout and nothing on stderr; compact smoke-test argument errors follow the same contract. Human-mode argument errors print a sanitized usage diagnostic on stderr. Unrecognized values are deliberately not reflected, which prevents terminal-control injection and accidental credential disclosure. A workflow group is successful only if every required child operation succeeds.

## Credential handling

For OpenClaw, inject `SERPER_API_KEY` through the skill entry/`primaryEnv`. For direct CLI automation, prefer a protected `SERPER_API_KEY`; `SERPER_API_KEYS` accepts a comma/newline-separated list when rotation is required. Environment sources take precedence and are not merged with the file; at most 32 deduplicated keys are accepted. A `config/serper.env` multi-key file is compatibility-only and intentionally does not satisfy OpenClaw's readiness gate. It must be a regular non-symlink file owned by the current user or root, have exactly one hard link, be no larger than 16 KiB, and have mode `0600` or stricter. Its before/opened/after/named-after stable metadata and the final identity of its opened parent directory must remain unchanged throughout the read.

Structured output exposes only a non-secret 1-based integer `keySlot` (or workflow `usedKeySlots`) based on deduplicated configuration order. It never includes key material; never enable diagnostics that print credentials.

Relative save names resolve beneath the skill's private `output/` directory, not the caller's cwd. Approved absolute roots are exactly `/tmp`, `/var/tmp`, the skill `runtime/`/`output/` directories, and a pre-existing absolute `GOOGLE_SEARCH_OUTPUT_DIR`; setting `TMPDIR` alone does not authorize another root. Search pretty/JSON/raw stdout is fully buffered and capped at 16 MiB before emission, so an oversized result is never partially printed; every saved UTF-8 file has the same cap. Secure saves use a same-directory temporary file, `fsync`, and atomic replacement at mode `0600`, and reject symlink parents/targets, unsafe parent ownership/modes, foreign-owned targets, and hard-linked targets.
