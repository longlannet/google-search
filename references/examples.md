# Examples

In `SKILL.md`, replace `{baseDir}` with the resolved skill directory. The shorter commands below assume the repository/skill root is the current directory.

```bash
/bin/bash -p scripts/run.sh web "OpenClaw agent framework" --num 5
/bin/bash -p scripts/run.sh news "OpenAI" --json --compact
/bin/bash -p scripts/run.sh images "Shanghai skyline" --limit 5
/bin/bash -p scripts/run.sh videos "Python tutorial" --json
/bin/bash -p scripts/run.sh places "coffee Shanghai"
/bin/bash -p scripts/run.sh maps "coffee Shanghai"
/bin/bash -p scripts/run.sh reviews --place-id "ChIJ..." --json
/bin/bash -p scripts/run.sh scholar "retrieval augmented generation" --page 1
/bin/bash -p scripts/run.sh patents "solid state battery"
/bin/bash -p scripts/run.sh autocomplete "opencl"
/bin/bash -p scripts/run.sh webpage "https://openclaw.ai" --sanitized-json
/bin/bash -p scripts/run.sh lens "https://example.com/public-image.jpg" --json
/bin/bash -p scripts/run.sh maps-reviews "coffee Shanghai" --pick 2 --limit 3
```

Do not put tokens, signed query parameters, private hostnames, or authenticated pages into URL endpoints. URL query strings are rejected by policy.
