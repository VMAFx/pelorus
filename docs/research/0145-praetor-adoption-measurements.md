<!-- markdownlint-disable MD013 MD060 -->
# Research digest 0145 — Praetor governance adoption

Evidence for
[ADR-0145](../adr/0145-praetor-governance-adoption.md). Measurements use the
integrated Pelorus tree at `6ccc73001dee07071b5dc8cda9ee2fcb9bfb4ac2` and
Praetor commit `846da5908d15b3cf5581ca6b0205cc644b249599`. Generation ran in
an isolated clone on 2026-09-20; no adoption command ran against the linked
working tree or its shared Git hooks.

## Declared policy

| Input | Value |
|---|---|
| Profile | `native-gpu-systems` |
| Facets | `security:high`, `api:public-contract`, `docs:seo-portal`, `agent:sandboxed` |
| Declined surfaces | `branch-ruleset`, `dev-container`, `git-hooks` |
| Repository identity | `VMAFx/pelorus` |
| Default branch | `master` |

The generated lock contains one profile digest and four facet digests. The
baseline contains 77 legacy findings:

| Rule | Count |
|---|---:|
| HISS-01 | 31 |
| HISS-02 | 1 |
| HISS-04 | 45 |
| **Total** | **77** |

Initial dry-run and materializing runs on the pre-review integration tree both
reported 74. Final integration review changed FFmpeg patch sources and raised
the measured total to 77, so the pinned baseline tool recorded a one-time
initial-adoption increase rationale against `6ccc730`. No finding was cleared by
this onboarding through product C, header, shader, or FFmpeg patch edits. The
baseline is a non-regression ratchet, not a claim that Pelorus has zero debt.

## Generated and reconciled surface

The materializing adoption created 40 files and reconciled 12 existing files.
Generated declarations include `.standards.yaml`, `.standards.lock`, the
baseline, profile/facet snapshots, editor integrations, labels, and Paperclip
configuration. Project reconciliation then established:

- `AGENTS.md` as the canonical repository guide;
- eight canonical reviewer personas under `.agents/agents/`;
- six synchronized vendor context projections;
- 32 synchronized persona projections across Claude, Codex, GitHub, and Gemini;
- retained Pelorus-native Codex role definitions and relocatable hook commands;
- a Meson/C/GLSL-native `Makefile` and `lefthook.yml`; and
- a pinned baseline-only GitHub Actions workflow for pull requests and
  `master` pushes.

Generated editor assumptions were not accepted blindly. The clangd compilation
database points at `build`, absent local `standards-lsp` commands were removed,
and editor tasks invoke the repository Make targets. Paperclip uses Pelorus's
`master` rebase target and a standard GitHub branch push, not generated `main`
and Gerrit-style `refs/for/*` defaults.

The generated comment-only `.gitleaks.toml` was replaced with an explicit
`[extend] useDefault = true` configuration. Gitleaks treats the generated file
as an empty ruleset: a deterministic AWS-token probe exits zero with that file
and exits one with Pelorus's adapted configuration. Praetor issue 410 owns the
generator defect.

The declined surfaces remain absent: no `.github/rulesets/main.json`, no
`.devcontainer/`, and no Praetor-generated hook or checkpoint bundle. An
adapted Lefthook configuration is tracked, but installation requires the
explicit `make hooks-install` command.

## Verification evidence

The canonical guide and all eight personas pass Praetor's Caveman check.
`compile-context --verify` reports every repository context and all 32 persona
projections synchronized.

`make verify-native` exits successfully on the integrated tree:

- Meson configure and Ninja build pass;
- 26 of 26 fast tests pass;
- clang-format passes;
- the configured clang-tidy policy exits zero; and
- changelog rendering is current.

Clang-tidy still prints two pre-existing `readability-function-size`
advisories in `libpelorus/src/interop.c` (`pel_blob_pack` and
`pel_blob_find_section`). This adoption does not hide or refactor that product
debt; neither function is touched by the governance change.

The pinned `audit` command passes manifest, lock, catalog, 77-of-77 baseline,
cross-context, Caveman, and persona-projection checks. It then fails because it
requires `.github/rulesets/main.json` even though the manifest explicitly
declines `branch-ruleset`. This blocks `make verify-all` and the hosted
standards job. Pelorus does not add a false ruleset artifact, weaken policy, or
bypass the failure.

## Upstream ownership

Praetor defects and generator assumptions found during adoption are reported in
the Praetor tracker:

- [issue 408](https://github.com/CordanaLLM/praetor/issues/408): audit ignores
  the accepted branch-ruleset decline; this is the remaining gate blocker;
- [issue 407](https://github.com/CordanaLLM/praetor/issues/407): a local clone
  origin ending in `/.` produces repository identity `.`;
- [issue 365 comment](https://github.com/CordanaLLM/praetor/issues/365#issuecomment-5752782450):
  generated editor configuration hard-codes a Go layout and absent local LSP;
- [issue 202 comment](https://github.com/CordanaLLM/praetor/issues/202#issuecomment-5752845780):
  editor generation injects unsupported Go tools and inspections into a C/GLSL
  consumer;
- [issue 321 comment](https://github.com/CordanaLLM/praetor/issues/321#issuecomment-5752834815):
  generated Paperclip output injects invalid forge, language, and threshold
  assumptions; and
- [issue 68 comment](https://github.com/CordanaLLM/praetor/issues/68#issuecomment-5752866367):
  canonical harness generation injects Go/Rust and unverified enforcement
  claims into native-GPU consumers;
- [issue 235 comment](https://github.com/CordanaLLM/praetor/issues/235#issuecomment-5752878414):
  context compilation forcibly restores references to skills the consumer does
  not receive; and
- [issue 410](https://github.com/CordanaLLM/praetor/issues/410): generated empty
  Gitleaks configuration disables all built-in secret rules; and
- [issue 380](https://github.com/CordanaLLM/praetor/issues/380): generated
  personas can fail Praetor's own Caveman check.

Earlier consumer findings about default-branch assumptions, untested
devcontainers, Go-specific hooks, and false compliance claims were added to
Praetor issues 71, 75, 242, and 370. They were not fixed in the Pelorus tree.

## Operational limits

No remote synchronization or repository-rule mutation ran. No Git hook was
installed. Active non-sample hooks in the shared Git directory were empty both
before and after isolated generation. Hosted GitHub Actions acceptance remains
unproven, and the standards workflow is expected to expose issue 408 until the
pinned Praetor behavior is corrected.

Reproduction entry points:

```bash
PRAETORCTL=/home/kilian/.cache/praetor-bin/standardsctl-846da590 make verify-native
/home/kilian/.cache/praetor-bin/standardsctl-846da590 compile-context --verify
/home/kilian/.cache/praetor-bin/standardsctl-846da590 audit
```
