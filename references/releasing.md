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

The local `scripts/check.sh` gate performs syntax, focused contract tests, and optional ShellCheck. It does not execute installer code, extract shell from documentation, test GitHub publication, or claim to prove security.

Before release, verify in isolated CI:

```bash
/bin/bash -p scripts/check.sh --shellcheck /trusted/path/shellcheck
```

Run the online smoke job separately with a scoped Serper key. A release checklist should record the commit, tag, CI run, and artifact hashes without embedding credentials.
