<!-- markdownlint-disable MD013 -->
# Pull request changelog render guard

**Date:** 2026-11-10

**Decision:** [ADR-0251](../adr/0251-changelog-render-owed-on-pull-requests.md).

**Scope:** pull request changelog render guard in `.github/workflows/ci.yml`, `scripts/release/check-changelog-staleness.py`, and `scripts/check-build-config.py`, measured on `8712f8d` / `270ff12`, git 2.56.0 scratch repository, GitHub Actions CI runs 42128311372, 42611731897, 42628208691, scheduled runs 42485108277, 42528716959, 42574014153, and pull requests #540 and #586.

## Verdict

Pull request CI fails in the `docs` job when the default branch owes a changelog render for more than 20 h judged on the merge commit's first parent, surfacing debt 4 h before `master` turns red at 24 h and directing authors to land the render PR first. Render pull requests are restricted to `CHANGELOG.md` only (or release files and optional fragment deletions at a release cut), preventing accidental fragment or code edits. `scripts/check-build-config.py` enforces step presence, command syntax, execution condition, and key allow-lists on the step, the `docs` job and the `ci.yml` top level, plain job keys set once in every workflow, rejecting 29 planted mutations.

## Measurements

| Probe | Result |
| --- | --- |
| CI run 42128311372 (2026-11-05) | red on `master` push after PR #540; render owed 26.1 h since `e9027a406c38` |
| CI run 42611731897 (2026-11-09) | red on `master` push; render owed 27.1 h since `f9779b431356` |
| CI run 42628208691 (2026-11-09) | red on `master` push; render owed 28.9 h since `f9779b431356` |
| Scheduled run 42485108277 | green on `master`; printed `warning: render owed` |
| Scheduled run 42528716959 | green on `master`; printed `warning: render owed` |
| Scheduled run 42574014153 | green on `master`; printed `warning: render owed since ... (21.7 h)` |
| Render PR #586 (`fc498c8`) | rewrote `changelog.d/fixed/soak-showinfo-nostats.md` alongside `CHANGELOG.md`; no existing check blocked the modification |
| git 2.56.0 scratch repo, merge commit committer date | `git log --first-parent -- ':(glob)changelog.d/*/*.md'` on a `--no-ff` merge adding a fragment lists the merge commit itself with merge build time (`merge 1000900000`), dating fragments to merge build time; requires judging the first parent (base tip) |
| Self-test p1: base owes 21 h, PR adds fragment | exit 1; stderr contains `limit 20 h` and `land the render PR first` |
| Self-test p2: base owes 19 h, PR adds fragment | exit 0; stdout contains `warning: render owed since` |
| Self-test p3: render PR (CHANGELOG.md only, current), base owes 21 h | exit 0; stdout contains `changelog render current` |
| Self-test p4: rerun with base current, PR fragment committed @ now-48 h, merge @ now-48 h | `check_pull_request` exit 0 (`fragments only`); `check_staleness(..., 20)` exit 1 |
| Self-test p5: render PR modifying existing fragment (merged tree renders current) | exit 1; stderr names `changelog.d/fixed/p5.md (M)` and gives scope remedy |
| Self-test p6: render PR with extra file `README.md` | exit 1; stderr names `README.md (A)` and gives scope remedy |
| Self-test p7: release cut (delete consumed fragments + version file `meson.build` as M) | exit 0 |
| Self-test p8: release cut + added fragment (merged tree renders current) | exit 1; stderr names `changelog.d/added/p8-new.md (A)` |
| Self-test p9: render PR whose merge tree is stale | exit 1; stderr contains `merge the default branch and run concat-changelog-fragments.sh --write again` |
| Self-test p10: root commit (`master`) and one-parent ref (`base-p1`) | exit 1 for each; stderr contains `is not a merge commit with two parents` |
| Self-test p11: diff status NUL-field allow-list strict parser | exit 0; pure-function validation of A, D, M, T allow-list and rejection of R100, junk, missing NUL |
| Self-test p12: release cut with non-ASCII fragment (`p12-ä.md`) | exit 0; `-z` byte shape avoids octal escaping of unusual characters |
| Self-test p13: plain render PR deleting fragment and adding code | exit 1; stderr names `changelog.d/added/p13.md (D)` and `src/p13.c (A)` |
| Self-test p14: release cut adding `src/p14.c` and modifying `src/p14-old.c` | exit 1; stderr names `src/p14.c (A)` and `src/p14-old.c (M)` |
| Self-test p15: release cut with no fragment deletions (v0.4.0 final shape) | exit 0; stdout contains `changelog render current` |
| Self-test p16: release cut deleting release file (`AGENTS.md`) | exit 1; stderr names `AGENTS.md (D)` |
| Self-test p17: release file allow-list matches real repository files and compile-context projections | exit 0; verifies `VERSION_SITES`, `CONTEXT_PROJECTIONS`, and `RELEASE_DOCS` |
| `scripts/check-build-config.py` planted cases | 29 planted mutations refused: step removed, step renamed, step duplicated, `if:` inverted to `!=`, `if:` dropped, `continue-on-error: true` added, step shell, step working-directory, step env, job continue-on-error, job if, job defaults, job quoted `"continue-on-error"`, workflow top-level defaults, workflow top-level env, `--pull-request` dropped, `--max-age-hours 24`, `\|\| true` appended, command in echo, command in comment, exit 0 before command, exit 0 after command, heredoc wrapper, if-false wrapper, step run duplicated, job steps duplicated, top-level jobs duplicated, docs job duplicated, job quoted key |

## Sources

- [ADR-0251](../adr/0251-changelog-render-owed-on-pull-requests.md) (Pull requests fail while the default branch owes a changelog render for more than 20 h).
- [ADR-0245](../adr/0245-changelog-render-at-landing.md) (Changelog render moves to landing and release cut).
- GitHub Actions CI runs 42128311372, 42611731897, 42628208691, 42485108277, 42528716959, 42574014153.
- Pull requests #518, #540, #586, and issue #589.
