<!-- markdownlint-disable MD013 MD060 -->
# ADR-0258: Lint changed code in the pre-push gate: cppcheck finding delta and ruff format on changed lines

- **Status**: Accepted
- **Date**: 2026-11-14
- **Deciders**: Lusoris
- **Tags**: static-analysis, ci, testing

## Context

Codacy reported cppcheck `unreadVariable` on PR #619. No local gate caught it. Python format drift also reached master because `scripts/check-python-lint.sh` runs `ruff check`, which does not enforce the formatter.

At commit `881ec9b` on 2026-11-14, cppcheck 2.22.0 reported 39 findings over the 55 translation units in `.config/clang-tidy/lane-files.txt` in 3.8 seconds. Ruff 0.17.4 found format drift in 95 files, about 14,000 diff lines. Scanning all 224 tracked C sources and headers also produced standalone-header noise and FFmpeg macro errors. A whole-tree zero-findings rule would require substantial unrelated changes.

[ADR-0190](0190-gitleaks-every-ref.md) defers cppcheck until its existing findings have a baseline. [ADR-0243](0243-ruff-zizmor-local-gate.md) adds Ruff linting but omits formatting. This decision amends both.

## Decision

Add `scripts/check-changed-lint.py` to `make verify-native` through `changed-lint`, and to the CI `docs` job on pull requests. Compare the committed head with its merge base against `LINT_BASE`, defaulting to `AUDIT_BASE` (`origin/master`). Extract committed trees with Git archive and the tar data filter; ignore uncommitted files.

When C, headers or the lane list change, run cppcheck style analysis in both trees over each tree's declared units. Subtract finding multisets keyed by file, rule and whitespace-collapsed source line. Line moves preserve the baseline; extra occurrences fail. This dynamic baseline answers ADR-0190's precondition without a committed findings file. There is no suppression path or inline-suppression option.

Run `ruff format --check` over whole added Python files and added-line ranges in modified Python files. Use the head tree's `ruff.toml` and exclusions. Deletion-only hunks need no format check. Ruff ranges end exclusively. Existing drift outside changed ranges remains debt.

Exit 0 means clean, 1 means findings, and 2 means a tool, usage or Git error. Missing tools and unparsed cppcheck diagnostics fail closed. Print tool versions and step summaries. The tool-dependent self-test plants both failures and checks old debt, adjacent edits, moved findings and an unknown base. Mutation checks must detect removal of either step or replacement of text keys with line numbers. `make changed-lint` runs the self-test under `scripts/test-selftest-git-isolation.py`, because the pre-push hook exports `GIT_DIR` and a fixture repository must not write into the invoking one.

`--cppcheck` names the cppcheck program (Make variable `CPPCHECK`, default `cppcheck`). The CI step passes it, so the engine's declared-linters audit sees cppcheck run by a workflow and the `.standards.yaml` cppcheck exception goes. Cppcheck is a new local dependency in `.devcontainer/base/Containerfile` and the CI `docs` job. It wins because it already runs in Codacy, is packaged by Debian and Ubuntu, catches the observed class, and takes 3.8 seconds on the declared units. The FFmpeg-tree lane remains outside this scope because it needs FFmpeg build headers; Codacy remains its cppcheck barrier.

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| Fix all 39 cppcheck sites and reformat 95 files, then require zero findings | Runner-up. Large unrelated churn, including mirrored files governed by `libpelorus/mirror-paths.txt`. |
| Commit a findings baseline file | Line moves invalidate locations; the baseline needs its own ratchet. |
| Rely on Codacy only | Reports after push and cannot refuse a local push; the requirement calls for a local gate. |
| Add lizard or more clang-tidy checks instead | Clang-tidy already runs through `Makefile`; it missed the PR #619 case. |

## Consequences

- Positive: `scripts/check-changed-lint.py --self-test` demonstrates local refusal of each planted failure without requiring unrelated cleanup.
- Negative: `Makefile` now requires cppcheck and two analysis passes for C changes. Existing findings and format drift remain in committed trees.
- Neutral: `.config/clang-tidy/lane-files.txt` remains the C coverage boundary from [ADR-0168](0168-praetor-engine-492a00f.md); `docs/development/build.md` documents the FFmpeg gap and repair commands.

## References

- [ADR-0190](0190-gitleaks-every-ref.md): cppcheck baseline precondition.
- [ADR-0243](0243-ruff-zizmor-local-gate.md): Ruff and zizmor local gate.
- [ADR-0168](0168-praetor-engine-492a00f.md): declared translation-unit coverage.
- PR #619: `unreadVariable` missed before push.
- Source: req (paraphrased, 2026-11-14, controller order pelorus-gate-cppcheck-ruff-format-20261114): add a local cppcheck step (unreadVariable class at least) and ruff format --check to the pre-push gate, with a planted case for each.
