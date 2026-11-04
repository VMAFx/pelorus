<!-- markdownlint-disable MD013 -->
# Ruff and zizmor in the local gate

**Date:** 2026-11-04

**Decision:** [ADR-0243](../adr/0243-ruff-zizmor-local-gate.md).

**Scope:** Ruff 0.17.3 and zizmor 1.32.0 from PyPI (hash-locked), actionlint 1.7.12, the Codacy tool and pattern configuration of VMAFx/pelorus read through the Codacy API on 2026-11-04, tree at 4ec7b80.

## Verdict

Codacy's 86 enabled Ruff patterns plus `SIM103`, `E721` and `PGH004` find eight issues in the tree, all fixable or answerable with a reasoned `noqa`. zizmor's default persona finds two low `self-repository` findings whose fix the pinned actionlint refuses. `S110` does not replace the empty-except check.

## Measurements

| Probe | Result |
| --- | --- |
| `ruff check` with the chosen rule set, tree at 4ec7b80 | 8 findings: 5 `B904`, 1 `S105`, 1 `S311`, 1 `S310` |
| Same, adding all of `B` and `SIM` | 31 more (20 `B905`, 7 `SIM105`, 2 `SIM102`, 1 `SIM108`, 1 `B007`); not adopted |
| Codacy Python issues on master by tool | Bandit `B603` 23, `B404` 14, Opengrep subprocess 13, Bandit `B101` 10, Pylint `C0123` 7, Lizard file length 5; Ruff 0 |
| `S110`, default settings, `except OSError: pass` | not flagged |
| `S110` with `check-typed-exception = true`, tree | 16 findings, all handlers that carry an explaining comment |
| `zizmor --offline` over `.github/workflows` | 25 findings, 23 suppressed by the default persona, 2 low `self-repository` (`release.yml:18`, `:29`), exit 12 |
| actionlint 1.7.12 on `release.yml` with `uses: $/.github/workflows/ci.yml` | refused: "not following the format owner/repo/path/to/workflow.yml@ref nor ./path/to/workflow.yml" |
| Ruff with `# nosec B603` on a `subprocess.run` line, `S603` selected | still flagged: Ruff reads only `noqa` |

## Limits

- No Ruff rule matches Pylint `C0123`; `E721` covers `==` comparisons of types only.
- zizmor runs `--offline`, so audits that query GitHub (impostor commits, known-vulnerable action versions) do not run locally.
