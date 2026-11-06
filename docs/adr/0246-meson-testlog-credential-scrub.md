<!-- markdownlint-disable MD013 -->
# ADR-0246: Meson testlog credential scrub and scanner

- **Status**: Proposed
- **Implementation**: pending (#547)
- **Date**: 2026-11-06
- **Deciders**: Lusoris
- **Tags**: security, build, test, meson, ci

## Context

`meson test` writes its inherited parent environment into `meson-logs/testlog.txt` and each test's environment to `testlog.json`. On developer workstations and shared hosts, inherited process environments carry secrets such as GitHub personal access tokens, CI tokens, messaging tokens, and API gateway keys. When tests run directly with `meson test`, these secrets are captured and persist inside the gitignored build directory, creating an exposure window for hosted agent lanes, local tools, and build artifact archives.

Workflow files under `.github/workflows/*.yml` keep their existing `meson test` lines unchanged because `scripts/check-ci-test-wiring.py` parses those command lines to verify test suite reachability, and CI runner environments rely on ephemeral, isolated runners. Local builds and script harnesses require active sanitization.

## Decision

We will implement a two-barrier defense against credential leakage in Meson test environments:

1. `scripts/run-meson-test.py`: A wrapper that strips credential-bearing environment variable names without inspecting values before invoking Meson (it resolves Meson to an executable regular file, then `os.execv` on POSIX or a child process wait on Windows). It scrubs explicit listed names and pattern-matched keys (`TOKEN(?!IZER)`, `SECRET`, `PASSWORD`, `PASSWD`, `CREDENTIAL`, `API_?KEY`, `ACCESS_KEY`, `PRIVATE_KEY`, `PASSPHRASE`, a `_KEY` suffix, `_PAT`, and `_AUTH`).
2. `scripts/check-testlog-credentials.py`: A scanner that walks checkout trees for `meson-logs/testlog*.{txt,json}` and reports lines holding credential-shaped values (GitHub classic and fine-grained tokens, `sk-` API keys, and AWS access key IDs), reporting paths and match counts without printing secrets; a symlinked build directory is scanned through its own `meson-logs` (exit 0 clean, 1 found, 2 error).
3. The Makefile `test` target and patch-stack integration harnesses (`ffmpeg-patches/test/build-and-run.sh`, `ffmpeg-patches/test/series-coexistence.sh`) route test runs through `scripts/run-meson-test.py` and run `scripts/check-testlog-credentials.py`; `make test` runs the scan even after a test failure and keeps the first failure status.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Fixed name list only (runner-up) | Simple explicit list with minimal pattern matching | Misses credentials exported under other names (messaging tokens, personal `*_PAT`, gateway keys) | Fails to protect against unlisted environment variables exported on developer hosts |
| Scrub in meson.build `env:` | Configured directly in build definitions | Cannot remove inherited process environment variables from `testlog.txt` | Meson `env:` only configures or sets test child environment, cannot clear parent environment captured by Meson's own runner |
| Shared fleet helper | Centralized implementation across fleet repositories | None exists yet; introduces external cross-repo dependency | Blocks immediate repository remediation while cross-repo tooling is undeveloped |

## Consequences

- **Positive**: Credential names are scrubbed before Meson runs, preventing secrets from appearing in `testlog.txt` and `testlog.json`. `scripts/check-testlog-credentials.py` detects leaks from any direct Meson runs.
- **Negative**: Any test requiring a credential-shaped variable must explicitly mock or set it within the test fixture itself.
- **Neutral / follow-ups**: Fast-suite test `scripts/test-meson-testlog-credentials.py` validates positive and negative name patterns, log scan detection, and end-to-end child environment scrubbing.

## References

- Precedent VMAFx/vmafx ADR-1333 / PR 3301 written in a code span (`VMAFx/vmafx#3301`).
- Precedent VMAFx/vmafx ADR-3400 (pattern scrub and log scanning).
- Maintenance ledger order `pelorus-meson-testlog-env-scrub-20261106` (paraphrased: scrub credential variables from the Meson test environment and gate it with a planted case).
