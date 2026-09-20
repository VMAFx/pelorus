<!-- markdownlint-disable MD013 MD060 -->
# ADR-0145: Adopt Praetor as a governance ratchet

- **Status**: Proposed
- **Date**: 2026-09-20
- **Deciders**: lusoris
- **Tags**: governance, agents, ci, hooks, documentation, standards

## Context

Pelorus and its VMAFx sibling share code, reviewers, release discipline, and a
public interop ABI, but only VMAFx currently carries Praetor's declared policy,
debt baseline, canonical agent contexts, and standards CI. Copying VMAFx's
generated files is unsafe: VMAFx has a Go/pre-commit build system, while Pelorus
uses Meson, C, GLSL, and an FFmpeg patch replay. Praetor also generates branch
and agent artifacts for `main`; Pelorus deliberately uses `master`.

Praetor commit `846da5908d15b3cf5581ca6b0205cc644b249599`, the revision
currently enforced by VMAFx, measures 77 existing Pelorus findings: 31 HISS-01,
one HISS-02, and 45 HISS-04. Its generated Makefile contains failing placeholder
build targets, generated Lefthook commands assume Go, and `adopt` installs hooks
into the shared Git directory when hook generation is enabled. Those outputs
need project-specific reconciliation before they can be treated as an
operational gate.

## Decision

We will adopt Praetor as scaffolding plus a ratcheting baseline, pinned to
`846da5908d15b3cf5581ca6b0205cc644b249599`, without refactoring product code to
clear legacy findings in the adoption change. Pelorus will use the
`native-gpu-systems` profile with `security:high`, `api:public-contract`,
`docs:seo-portal`, and `agent:sandboxed`. `AGENTS.md` and `.agents/agents/*.md`
become canonical sources for generated vendor contexts and reviewer personas.
`make verify-all` and Lefthook will invoke Pelorus's existing Meson, C, shader,
documentation, and governance gates. CI will verify compiled contexts and the
baseline on every pull request and push to `master`.

The manifest will explicitly decline `branch-ruleset` until Praetor can target a
repository's real default branch, `dev-container` until Pelorus has a tested
Vulkan-capable container, and `git-hooks` because its generated commands assume
Go and hook installation mutates shared Git state. No implementation command may
run `sync --remote`. Generated Paperclip artifacts will target `master`; no
checkpoint bundle is accepted while `git-hooks` remains declined. Pelorus will
own an adapted `lefthook.yml`; installation remains an explicit
`make hooks-install` action, so adoption and CI cannot mutate the repository's
shared `.git/hooks` directory.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Copy VMAFx's adoption verbatim | Small design effort; identical visible layout | Imports Go commands, VMAFx paths, `main`, an unrelated 1,411-finding baseline, and VMAFx personas | It would claim enforcement that Pelorus cannot execute |
| Run `praetorctl adopt --force` and commit every generated file unchanged | Maximum generated coverage | Failing Makefile, Go-only hooks, wrong branch targets, untested container, shared-hook side effect | Generated output is a starting point, not proof of a valid repository contract |
| Adopt declarations and CI only | Smallest change | Agent contexts, personas, hooks, and local verification continue to drift | HISS-16 and cross-tool consistency are primary adoption goals |
| Clear all 77 findings during adoption | Starts with a zero-debt baseline | Mixes governance scaffolding with broad product-code refactoring | A baseline ratchet prevents regression without destabilizing filter work |

## Consequences

- **Positive**: policy inputs and engine revision become reproducible; legacy
  debt may shrink but cannot grow; six agent contexts and reviewer personas gain
  canonical sources; `make verify-native` provides one Pelorus-native gate.
- **Negative**: generated contexts and persona projections add repository
  surface area; Praetor upgrades require a deliberate baseline comparison;
  contributors need Go 1.27 to install the pinned engine locally. The pinned
  auditor currently fails after all other checks because it requires the
  explicitly declined branch-ruleset artifact; `make verify-all` and hosted
  standards CI remain blocked by Praetor issue 408.
- **Neutral / follow-ups**: accept the unified gate only after Praetor issue 408
  is resolved; enable a generated branch ruleset only after Praetor supports
  `master`; design and test a Vulkan devcontainer separately; consider
  touched-file enforcement after legacy debt is low enough for routine changes.

## References

- VMAFx ADR-1249, `origin/master` at `371ff5891ad43b6d8072d9fac132349ee3ddaaa9`.
- Praetor commit `846da5908d15b3cf5581ca6b0205cc644b249599`.
- [Praetor issue 408](https://github.com/CordanaLLM/praetor/issues/408) — audit
  ignores an accepted `branch-ruleset` decline.
- [Praetor issue 407](https://github.com/CordanaLLM/praetor/issues/407) — local
  clone identity can render as `.`.
- [Praetor issue 365](https://github.com/CordanaLLM/praetor/issues/365) —
  generated editor configuration assumes repository paths and a locally built
  language server.
- [Praetor issue 202](https://github.com/CordanaLLM/praetor/issues/202) — editor
  and linter generation is not consumer-selectable or language-aware.
- [Praetor issue 321](https://github.com/CordanaLLM/praetor/issues/321) —
  Paperclip output ignores effective consumer policy.
- [Praetor issue 68](https://github.com/CordanaLLM/praetor/issues/68) — generated
  harness policy is not language- or profile-specific.
- [Praetor issue 235](https://github.com/CordanaLLM/praetor/issues/235) — context
  compilation references skills not shipped to consumers.
- [Praetor issue 410](https://github.com/CordanaLLM/praetor/issues/410) — native
  adoption creates an empty Gitleaks ruleset that disables default detection.
- [Praetor issue 380](https://github.com/CordanaLLM/praetor/issues/380) —
  generated personas can fail Praetor's own Caveman check.
- [ADR-0108](0108-deep-dive-deliverables-rule.md) — adoption deliverables.
- Source: `req`, 2026-09-20: "we need to onboard praetor as vmafx did (mostly) already" and approval to proceed with the staged implementation.
