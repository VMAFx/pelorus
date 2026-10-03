<!-- markdownlint-disable MD013 -->
# ADR-0154: Re-pin the Praetor engine to `bf815ba5`

- **Status**: Accepted
- **Date**: 2026-10-03
- **Deciders**: Lusoris
- **Tags**: governance, ci, standards

## Context

[ADR-0145](0145-praetor-governance-adoption.md) pinned the Praetor engine at
`25451d888c8710822dd578907625dc69a0975142` and is Accepted, so a new pin needs
its own record. By 2026-10-03 upstream `main` had moved 52 commits to
`bf815ba551fe326447f3161f97ba69ae5b7fe919`. Several of those commits are
breaking for adopters:

- the HISS scanner reads shell scripts, systemd units, Ansible, and
  JavaScript/TypeScript (Praetor pull requests 672 and 694);
- baseline entries are keyed by rule, file, and function instead of line, so
  code that only moved is no longer reported as new (pull request 749);
- a re-adoption keeps an existing baseline byte for byte and reports what
  `baseline --verify` says, instead of rescanning over it (pull request 745);
- the locked documentation gate moves to `js-yaml` 5.4.2 and drops the
  `markdownlint-cli2` wrapper. `npm audit --package-lock-only` drops from 8
  advisories (6 high, among them `braces` and `smol-toml`; 2 moderate,
  `js-yaml` and `markdown-it`) to none;
- `version` names the build commit for a `go install` build (pull request 722,
  which fixes Praetor issue 642).

Renovate does not update engine-managed files (`renovate.json` disables them),
so they change only through an engine re-pin.

## Decision

Pin Praetor `bf815ba551fe326447f3161f97ba69ae5b7fe919` in
`.github/workflows/standards-gate.yml` and every document that quotes the pin.
Use ADR-0145's upgrade path: run `adopt --force
--lock-source-root=<praetor at the pin>` in a throwaway clone, then reconcile
the output.

- Accept the re-pinned `.standards.lock`, the `api:public-contract` facet text,
  the documentation-gate assets and npm lockfile, the label taxonomy wording,
  the managed VS Code values, the README governance block, and the Renovate
  entries for the (declined) DevContainer files.
- Keep the Pelorus `AGENTS.md` harness. The regenerated generic harness marks
  every invariant "not enforced" and drops Pelorus rules such as one-level
  `goto fail` cleanup, the same reason ADR-0145 gave. `compile-context` updates
  only the renderer-owned register block.
- Keep `.zed/settings.json`. The merge adds Go language settings to a C/GLSL
  repository (Praetor issue 202).
- Re-record the baseline with `baseline --record --allow-increase --reason`.
  The new scanner reports 37 findings in code this change does not touch: 36
  HISS-07 `|| true` sites in shell scripts, and one HISS-02 `curl` with no
  deadline in `scripts/bench/fetch-corpus.sh`. The total goes from 62 to 99.
  The hosted audit shows the recorded reason as a HISS-13 warning.

The declines (`branch-ruleset`, `dev-container`, `git-hooks`) stay as they
are. Lifting them is pull request 67's decision, not this one's.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Re-pin, record the newly visible debt, fix it in a follow-up | Mechanical, reviewable governance change; required gate stays green | Baseline grows for a while | **Chosen** |
| Re-pin and fix all 37 script findings in the same change | No temporary baseline growth | `generate.sh` and `build-and-run.sh` changes need a full FFmpeg replay, which mixes behaviour changes into a governance bump | Harder to review; delays the advisory fixes |
| Stay on `25451d88` | No work now | Keeps vulnerable documentation-gate dependencies; the gap to upstream keeps growing | Debt compounds |
| Fold the re-pin into pull request 67 (full adoption) | One re-render | Blocks the re-pin on the DevContainer build, the hook proof, and signed-commit rulesets | Couples unrelated risks |

## Consequences

- **Positive**: the gate runs current engine checks, shell-script debt is
  visible and ratcheted, and the documentation gate's lockfile audits clean.
- **Negative**: the baseline records 37 more findings until the script-hardening
  follow-up retires them. At least two of them are real defects, not style: the
  unbounded `curl` without `--fail`, and the `mv … || true` rename loop in
  `ffmpeg-patches/generate.sh` that can hide a stale patch.
- **Neutral**: ADR-0145 stays Accepted. This ADR supersedes only its engine pin
  and its baseline numbers.

## References

- [ADR-0145](0145-praetor-governance-adoption.md) and
  [research digest 0154](../research/0154-praetor-repin-bf815ba5.md).
- Praetor `25451d88..bf815ba5`: pull requests 639–758 on
  `github.com/cordanaLLM/praetor`.
- Source: user request 2026-10-03, paraphrased: Praetor upstream moved a lot,
  so the adoption needs updating as well.
