# Release Boundary

The skill repository does not implement its own release security platform.

Required release controls belong in GitHub or another isolated CI system:

- protected default branch and required checks
- non-root disposable runner
- repository mounted read-only where practical
- default-deny network for offline tests
- explicit network-enabled job for Serper smoke tests
- signed annotated version tag
- immutable release assets and recorded checksums

The local `scripts/check.sh` gate performs per-file syntax checks, offline regression tests, and optional ShellCheck. Installer fixtures execute a temporary copy with local package stubs and injected signals to exercise locks, publication, rollback, and read-only checks. They never install into the live runtime or contact package indexes. This gate does not test real dependency downloads or GitHub publication, and does not claim to prove security.

Before release, verify in isolated CI:

```bash
/bin/bash -p scripts/check.sh --shellcheck /trusted/path/shellcheck
```

Run the online smoke job separately with a scoped Serper key. A release checklist should record the commit, tag, CI run, and artifact hashes without embedding credentials.
