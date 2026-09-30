<!-- markdownlint-disable MD013 -->
# ADR-0145: Adopt Praetor as a governance ratchet

- **Status**: Proposed
- **Date**: 2026-09-20 (revised 2026-09-30 for the engine re-pin)
- **Deciders**: lusoris
- **Tags**: governance, agents, ci, hooks, documentation, standards

This ADR stays Proposed while PR 56 is open. Its body was revised in place for
the engine re-pin and the review fixes, which the index allows only before
acceptance. It becomes Accepted when the maintainer merges the pull request,
which includes accepting the new `agent-hooks` decline.

## Context

Pelorus and its VMAFx sibling share code, reviewers, release discipline, and a
public interop ABI, but only VMAFx currently carries Praetor's declared policy,
debt baseline, canonical agent contexts, and standards CI. Copying VMAFx's
generated files is unsafe: VMAFx has a Go/pre-commit build system, while Pelorus
uses Meson, C, GLSL, and an FFmpeg patch replay.

The first adoption pinned Praetor `846da5908d15b3cf5581ca6b0205cc644b249599`,
the revision VMAFx enforced at the time. It measured 78 findings (31 HISS-01,
one HISS-02, 46 HISS-04). Its audit then failed on every run because it
required the branch-ruleset artifact that the manifest explicitly declines
(Praetor issue 408). Several HISS-04 findings were misparses: an `AVOption`
table reported as a function named `FLAGS`, and an `extern "C"` block reported
as a 492-line function.

By 2026-09-30 Praetor main had moved 162 commits to
`25451d888c8710822dd578907625dc69a0975142`, several of them breaking for
adopters. The changes that reach Pelorus are:

- the audit honours an accepted `branch-ruleset` decline (issue 408, closed by
  Praetor pull request 424);
- native function blocks are classified structurally and HISS-04 length is
  measured from the opening brace, which removes the five misparses above and
  names nine functions the old scanner reported as `{` (Praetor pull requests
  422 and 423);
- the scanner now reports HISS-07 for `sys.exit` outside a Python `__main__`
  entry point;
- the `docs:seo-portal` facet now brings a locked documentation gate: vendored
  `tools/markdownlint/` and `tools/figures/` assets, a
  `praetor-docs.yml` workflow, a `Makefile` block, and a managed
  `.gitattributes` block, compared byte for byte by the audit (Praetor pull
  request 418);
- the default text-register block that `compile-context` renders into
  `AGENTS.md` changed, and the audit now lints non-Markdown agent-facing
  strings (the Paperclip harness) in the internal register through
  `register.sources` (Praetor pull requests 420 and 487);
- the manifest can declare `repository.default_branch`, so rulesets target
  `master` (Praetor pull request 547);
- adoption registers a `praetorctl hook <client> pre-tool` interceptor in the
  Claude, Codex, and Gemini settings unless `agent-hooks` is declined
  (Praetor pull request 495);
- the pinned catalog changed values: `gocyclo` left `agent:sandboxed` and
  `oapi-codegen` left `api:public-contract`, both Go tools that Pelorus never
  ran. The other catalog files changed layout only.

The new engine still fails a local audit on the declined `git-hooks` step: it
requires `.git/hooks/pre-commit` unless `CI` is set (Praetor issue 175). Its
`version` command cannot identify a `go install` build (Praetor issue 642).

## Decision

We will pin Praetor `25451d888c8710822dd578907625dc69a0975142` and keep using
it as scaffolding plus a ratcheting baseline, without refactoring product code
to clear legacy findings in the adoption change. Pelorus keeps the
`native-gpu-systems` profile with `security:high`, `api:public-contract`,
`docs:seo-portal`, and `agent:sandboxed`. `AGENTS.md` and `.agents/agents/*.md`
stay the canonical sources for generated vendor contexts and reviewer personas.
`make verify-all` and Lefthook invoke Pelorus's Meson, C, shader,
documentation, and governance gates. CI verifies compiled contexts, the
baseline, and the documentation gate on every pull request.

The hosted audit compares each change with its target. It runs
`standardsctl audit --base <target> --touched-debt-delta-reason <reason>`,
then `standardsctl baseline --verify`. `--base` turns on the engine's HISS-13
growth guard: the committed baseline may not record more findings than the
target's, unless the increase was recorded with
`baseline --record --allow-increase --reason`, which the job prints as a
warning. The debt-delta reason keeps the touched-file rule from revoking a
file's baselined findings; a touched file fails only when one of its rules
gains a finding. `baseline --verify` then requires every current finding at its
current line, so a stale fingerprint cannot reach `master`. Touched-file
zero-debt mode stays deferred.

The upgrade follows the engine's documented path. `adopt --force
--lock-source-root=<praetor checkout at the pin>` runs in a throwaway copy of the
repository, so no command touches the shared `.git` directory; its output is
then reconciled:

- accept the re-pinned `.standards.lock` and catalog, the rescanned baseline,
  the documentation gate assets and blocks, the `register.sources` coverage,
  the managed `.gitignore` and README blocks, and the Renovate and actionlint
  entries for engine-managed files;
- keep the Pelorus `AGENTS.md` harness instead of the regenerated generic one,
  which states that every invariant is unenforced and drops Pelorus-specific
  rules such as one-level `goto fail` cleanup; `compile-context` only adds the
  register block;
- keep the reconciled editor settings instead of merges that add Go language
  settings to a C/GLSL repository (Praetor issue 202);
- reword three Paperclip contract strings to the engine's own current wording
  so they pass the internal-register lint;
- make the legacy Markdown conform to the locked rule set (mostly table
  delimiter spacing), without excluding any file from the gate;
- accept the engine's canonical `.config/labels.yaml` taxonomy (14 labels).
  The audit only checks that the file exists, and nothing syncs it to the
  forge until `sync --remote` runs.

`CLAUDE.md` becomes a projection of `AGENTS.md`. Its former hand-written
Claude Code guide (the forge default, skills and hooks inventory, project
state, repository layout, and per-commit sync rules) moves into the Pelorus
section of `AGENTS.md`, so every vendor context receives it.

The manifest declares `repository.default_branch: master`. It keeps declining
`branch-ruleset`, `dev-container`, and `git-hooks`, and now declines
`agent-hooks`. The engine can now render a `master` ruleset, but the rendered
ruleset requires signed commits and a policy that the live repository does
not enforce. Committing it would claim protection that `sync --remote` has not
applied, so enabling it stays a separate, forge-mutating decision. The
devcontainer still needs a tested Vulkan-capable image. Git hook installation
mutates the Git directory that every linked worktree shares. Agent-hook
registration would run `praetorctl` on every agent tool call, on machines that
may not have it on `PATH`. No command runs `sync --remote`.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Stay on `846da590` | No new surface; no documentation churn | Audit fails on every run (issue 408); scanner misparses stay in the baseline; drifts further from VMAFx and upstream | Keeps a gate that can never pass |
| Copy VMAFx's adoption verbatim | Small design effort; identical visible layout | Imports Go commands, VMAFx paths, an unrelated 1,411-finding baseline, and VMAFx personas | It would claim enforcement that Pelorus cannot execute |
| Commit `adopt --force` output unchanged | Maximum generated coverage; no reconciliation | Replaces the Pelorus harness with a generic one, adds Go editor settings, registers agent hooks that call an engine contributors may lack | Generated output is a starting point, not proof of a valid repository contract |
| Exclude legacy docs through `documentation.style_exclude` | No churn in 50 Markdown files | Removes most of the public documentation from the gate that the declared facet requires | A bypass of declared policy; the violations are mechanical to fix |
| Accept `branch-ruleset` now that `master` is supported | Audit compares a real ruleset | Commits a signed-commit ruleset that the forge does not apply; misstates the live protection | Needs its own decision and a `sync --remote` run by the maintainer |
| Clear all 75 findings during adoption | Starts with a zero-debt baseline | Mixes governance scaffolding with broad product-code refactoring | A baseline ratchet prevents regression without destabilizing filter work |

## Consequences

- **Positive**: policy inputs and engine revision are reproducible; the hosted
  Standards job can pass; legacy debt may shrink, and the hosted job fails a
  baseline that grows against its target unless the increase carries a
  recorded reason; the baseline no longer carries scanner misparses; six agent contexts and eight
  reviewer personas have canonical sources; public Markdown is linted by a
  locked rule set; `make verify-native` stays the Pelorus-native product gate.
- **Negative**: the engine's managed assets add about 8,400 vendored lines
  under `tools/` that only `adopt` may refresh; `make verify-all` now needs
  Node.js 22+ as well as Go 1.27; the Markdown conformance pass edits 50
  existing documents (formatting only). Local `make audit` and
  `make verify-all` exit 1 on the missing pre-commit hook until Praetor issue
  175 is fixed; CI skips that check. `standardsctl version` cannot prove the pin
  (Praetor issue 642); CI prints Go's module version instead. The declared
  profile and facets carry controls that nothing in the repository executes:
  SLSA 3 provenance, cosign signatures, and an SBOM; signed commits, two
  approving reviews, and stale-review dismissal; the `semgrep`, `cppcheck`,
  `clippy`, `gitleaks`, `trivy`, `buf`, and `spectral` linters; and the
  `docs:seo-portal` site checks (JSON-LD, sitemap, `robots.txt`, Core Web
  Vitals). The audit does not check them, so they are declared only. For C,
  HISS-04 enforces only the 60-line cap; the engine measures cyclomatic,
  cognitive, and statement complexity for Go alone.
- **Neutral / follow-ups**: re-run the upgrade path on the next pin and compare
  the baseline per rule; decide on a branch ruleset together with
  `sync --remote`; design and test a Vulkan devcontainer separately; consider
  touched-file zero-debt mode once legacy debt is low enough for routine
  changes; make `Standards` a required status check (a forge setting outside
  this change); implement or drop each declared-only control listed above.

## References

- VMAFx ADR-1249, `origin/master` at `371ff5891ad43b6d8072d9fac132349ee3ddaaa9`.
- Praetor commit `25451d888c8710822dd578907625dc69a0975142` (current pin) and
  `846da5908d15b3cf5581ca6b0205cc644b249599` (first adoption).
- [Research digest 0145](../research/0145-praetor-adoption-measurements.md) —
  measurements and per-rule baseline comparison.
- [Praetor issue 408](https://github.com/cordanaLLM/praetor/issues/408) — audit
  ignored an accepted `branch-ruleset` decline; closed, fixed at the new pin.
- [Praetor issue 175](https://github.com/cordanaLLM/praetor/issues/175) — audit
  ignores an accepted `git-hooks` decline outside CI.
- [Praetor issue 642](https://github.com/cordanaLLM/praetor/issues/642) —
  `version` cannot identify a `go install` build.
- [Praetor issue 29](https://github.com/cordanaLLM/praetor/issues/29) —
  baseline fingerprints are keyed by line number.
- [Praetor issue 71](https://github.com/cordanaLLM/praetor/issues/71) — remote
  governance assumes `main`.
- [Praetor issue 407](https://github.com/cordanaLLM/praetor/issues/407) — local
  clone identity can render as `.`.
- [Praetor issue 202](https://github.com/cordanaLLM/praetor/issues/202) — editor
  and linter generation is not consumer-selectable or language-aware.
- [Praetor issue 321](https://github.com/cordanaLLM/praetor/issues/321) —
  Paperclip output ignores effective consumer policy.
- [Praetor issue 235](https://github.com/cordanaLLM/praetor/issues/235) — context
  compilation references skills not shipped to consumers.
- Closed at or before the new pin: issues
  [68](https://github.com/cordanaLLM/praetor/issues/68),
  [365](https://github.com/cordanaLLM/praetor/issues/365),
  [380](https://github.com/cordanaLLM/praetor/issues/380), and
  [410](https://github.com/cordanaLLM/praetor/issues/410).
- [ADR-0108](0108-deep-dive-deliverables-rule.md) — adoption deliverables.
- Source: `req`, 2026-09-20: "we need to onboard praetor as vmafx did (mostly) already" and approval to proceed with the staged implementation; 2026-09-30: Praetor "moved", re-pin to `25451d88`.
