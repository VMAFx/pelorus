<!-- markdownlint-disable MD013 MD060 -->
# ADR-0220: In-Repo Pinned CodeQL Workflow for Code Scanning

- **Status**: Accepted
- **Implementation**: landed
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: ci, security, codeql, static-analysis, governance
- **Related**: [ADR-0145](0145-praetor-governance-adoption.md), [ADR-0171](0171-eupl-relicense.md)

## Context

GitHub code scanning on `VMAFx/pelorus` had no analysis configured (the API returned 404), with default setup disabled. Pelorus combines native C/C++ (`libpelorus`, FFmpeg filter sources), Python (build tooling, test runners, checkers, release automation), and GitHub Actions workflows.

Without automated static analysis, security vulnerabilities (such as memory safety violations, integer overflows, untrusted input handling, or workflow script injection) risk reaching `master` undetected. While `clang-tidy` runs in CI, it operates as a translation-unit linter rather than a full interprocedural taint-tracking and dataflow analyzer.

## Decision

We introduce an in-repo, pinned CodeQL workflow at `.github/workflows/codeql.yml` alongside an enforcement checker at `scripts/check-codeql-workflow.py` registered in the Meson fast test suite:

1. **Pinned actions**: Every action reference (`actions/checkout`, `github/codeql-action/init`, `github/codeql-action/analyze`) is pinned to an immutable 40-character commit digest with an inline `# v...` version comment (HISS-11).
2. **Language matrix**: Analyzes `c-cpp` (`build-mode: manual`), `python` (`build-mode: none`), and `actions` (`build-mode: none`) using the `security-and-quality` query suite.
3. **Probe isolation for C/C++**: For `c-cpp`, `meson setup --werror "${{ runner.temp }}/build"` executes *before* `codeql-action/init`. This ensures compiler capability probes and test feature checks are kept out of the CodeQL database; only the subsequent `ninja -C "${{ runner.temp }}/build"` build step is traced.
4. **Draft PR gate**: The first step under `steps:` is `Stop on a draft pull request`, failing immediately on draft PRs to conserve runner minutes. Every subsequent step executes only when `github.event.pull_request.draft != true`.
5. **Least-privilege permissions**: Top-level permissions declare `contents: read` only. Job-level permissions grant `security-events: write` solely so `codeql-action/analyze` can upload SARIF findings to GitHub code scanning.
6. **Trigger contract**: Runs on pull requests (opened, synchronize, reopened, ready_for_review), pushes to `master`, and a weekly scheduled cron.
7. **Required checks**: The three jobs report `CodeQL (c-cpp)`, `CodeQL (python)` and `CodeQL (actions)` on every pull request, so the Praetor audit derives them as required contexts. The committed ruleset `.github/rulesets/main.json` lists them (13 checks), as [ADR-0203](0203-arm64-ci-lane.md) did for the arm64 lane; the live ruleset changes only with the maintainer's `praetorctl sync --remote`.
8. **In-repo validation**: `scripts/check-codeql-workflow.py` validates this structure and executes an in-memory `--self-test` mutation battery in Meson's fast test suite.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| GitHub default setup | Zero workflow maintenance in repository | Unpinned action versions, no draft PR gating, unable to run pre-init Meson setup, configuration not version-controlled | Violates HISS-11 supply-chain pinning, wastes CI minutes on draft PRs, and pollutes database with build probe files |
| Semgrep only | Fast scanning, simple configuration | Lacks deep interprocedural taint tracking and semantic dataflow for C/C++ | Insufficient for comprehensive memory-safety and dataflow auditing of native C library code |
| None | Zero CI overhead | Code scanning remains blind (404); security flaws can land undetected | Unacceptable risk for public C library and GPU pre-encode pipeline |

## Consequences

- **Positive**: Automated CodeQL security analysis active for C/C++, Python, and GitHub Actions; SARIF results uploaded to GitHub code scanning; reproducible and pinned.
- **Negative**: Adds three CodeQL jobs to every non-draft pull request, every push to `master` and the weekly scheduled run; the C/C++ job repeats the Meson build of libpelorus.
- **Neutral / follow-ups**: Validated by `scripts/check-codeql-workflow.py` in Meson fast test suite; documented in `docs/development/build.md`.

## References

- `.github/workflows/codeql.yml`
- `scripts/check-codeql-workflow.py`
- [ADR-0145](0145-praetor-governance-adoption.md)
- [ADR-0171](0171-eupl-relicense.md)
