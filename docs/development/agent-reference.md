<!-- markdownlint-disable MD013 -->
# Agent reference tables

On-demand reference for coding agents. The always-loaded contract is the root `AGENTS.md`; it links here so its projections stay under each client's instruction-file cap. Keep both in step: a new top-level package adds a row to the layout table below.

## Enforcement here

Harness table = engine rendering; `not enforced` there = no engine-generated check. Checks wired in this repository:

| Rule | Decided here by |
| --- | --- |
| HISS-01 | audit `HISS invariant scan` (C scanner) + `c-reviewer`; baseline ratchet. One-level `goto fail` cleanup allowed; other new `goto` rejected |
| HISS-02 | audit `HISS invariant scan` (unbounded C loops) + `c-reviewer`; baseline ratchet. I/O deadlines not measured for C |
| HISS-03 | review only (`c-reviewer`) + tests; no scanner |
| HISS-04 | LOC <= 60: audit scan + baseline ratchet. Cyclomatic, cognitive, statements: `[REPORT]` only (Go only) + `c-reviewer`; clang-tidy size check advisory |
| HISS-05 | review only (`c-reviewer`); no analyzer |
| HISS-06 | review only; no analyzer; `sanitizers` CI job finds races, not pool bounds |
| HISS-07 | `pel_result` contract: clang-tidy (`make verify-native`) + `c-reviewer`; audit scanner reads Python/shell for this rule, not C |
| HISS-08 | audit scan (banned-call rule) + `c-reviewer` + build |
| HISS-09 | bound offset arithmetic before pointer formation: review only (`c-reviewer`, CERT C) + tests + `sanitizers` CI job |
| HISS-10 | audit `Build warnings (HISS-10)` + `clang-tidy translation-unit coverage` + `make verify-native`; every CI `meson setup` passes `--werror`; fails audit and CI |
| HISS-11 | audit `Supply chain (HISS-11)`: L3 from `release.yml`, SBOM from `release-build.yml`; reads workflow files only, not published attestations |
| HISS-12 | `scripts/check-gitleaks.sh` in required `docs` CI job: pinned gitleaks, every branch + tag, `--self-test` planted side-branch secret; exceptions = `.gitleaksignore` fingerprints only (ADR-0190). Not in `make verify-all` |
| HISS-13 | `baseline --verify` + audit ratchet (`--base`, touched-debt delta) in `make audit`, pre-commit, `standards-gate.yml`; fails CI |
| HISS-14 | footer: `commit-msg` hook (`forge check-message`) + CI `forge check-commits`. Append-only: `interop_test.c` fixture + `interop-abi-reviewer`. Hooks opt-in; CI authoritative |
| HISS-15 | fast suite (`meson test --suite=fast`) in `core` CI job + PR review; skip passes only when named (`check-test-skips.py`, ADR-0188) |
| HISS-16 | `compile-context --verify` (pre-commit `context-check`, `standards-gate.yml`) + audit context, persona, caveman checks |
| HISS-17 | Lefthook `post-commit` `state-sync` (hooks installed) + `session-start` hook; no gate fails on stale ledger |
| HISS-18 | audit `Workflow triggers (HISS-18)`: `[WARN]` only, 10 open findings in 5 of 8 workflows (#207) |
| HISS-19 | review only: `dedupe scan` reads Go only, none here; `dedupe-cadence` post-commit bookkeeping runs |
| HISS-20 | not wired: no `.config/hiss/coverage.yaml`, so `hiss coverage --verify` cannot run |
| HISS-21 | partial: `windows` CI job (MSYS2 UCRT64, fast suite); Make, shell, Lefthook gates Linux-first; no macOS leg |

Engine: pin = `PRAETOR_REF` in `.github/workflows/standards-gate.yml`. Make (`.config/praetor/engine.mk`) + Lefthook jobs run it via `.config/lefthook/engine.sh`: `go install` on first use into `${XDG_CACHE_HOME:-~/.cache}/praetor/engine/<pin>/`, never PATH, fail closed (ADR-0187). Direct engine call: `sh .config/lefthook/engine.sh <args>`. `PRAETORCTL=<path>` overrides make only, unchecked. Agent hooks call `praetorctl` by name: copy pinned `standardsctl` to `praetorctl` on `PATH`; missing binary -> hook exits 127 (bash) or 1 (cmd, PowerShell), client reports non-blocking error, shell call runs without Praetor policy. Docs gate needs Node 22.12+. Never install hooks or fake hook file to pass gate.

## Layout and ownership

| Path | Contract |
| --- | --- |
| `meson.build`, `meson_options.txt` | build root: libpelorus, tests, shaders |
| `LICENSE`, `LICENSES/`, `REUSE.toml` | EUPL-1.2 root text (bytes of `LICENSES/EUPL-1.2.txt`); every licence text in use; per-file copyright + licence, REUSE 3.3, default table first; audit checks table order + root licence (ADR-0171) |
| `libpelorus/include/pelorus/` | public API, version, append-only interop ABI |
| `libpelorus/src/` | core pack/parse + parameter logic |
| `libpelorus/test/` | ABI and API conformance; UTF-8 path test |
| `libpelorus/shaders/` | standalone reference shaders |
| `ffmpeg-patches/files/` | canonical FFmpeg host/filter sources |
| `ffmpeg-patches/files/vulkan/` | canonical shipped shader sources |
| `ffmpeg-patches/0001-*.patch` | generated artifacts; never hand-edit |
| `ffmpeg-patches/{generate.sh,series.txt,test/}` | regeneration, apply order (after shared series), replay + smoke gate; `test/sei_fit_test.c` + planted twin per rule via `scripts/plant-header.py` (ADR-0236) |
| `docs/adr/` | decisions; reserve via `scripts/adr/next-free.sh --claim <slug>` |
| `docs/{architecture,api,metrics,usage,backends,development}/` | human-readable surface docs |
| `docs/research/` | measured deep-dive evidence |
| `changelog.d/` | Keep-a-Changelog fragments |
| `tools/pelorus_qp_report.c`, `tools/pelorus_zones.c`, `tools/pelorus_qloop.c` | libpelorus CLI demonstrators; not installed |
| `tools/tester/` | tester report program (stdlib Python, `python3 -I`), JSON schema, `--self-test` (ADR-0173) |
| `tools/markdownlint/`, `tools/figures/` | Praetor-managed docs gate; refresh via `adopt` only |
| `scripts/` | ADR claim, bench, release, build-config + shader checks, shared-series fetch + verify (`fetch-ffmpeg-series.sh`, ADR-0185), header plant for fast-suite twins (`plant-header.py`, ADR-0236), secret scan (`check-gitleaks.sh`, ADR-0190), Windows tester zip packaging + PE import check (`windows/`, ADR-0213) |
| `Makefile`, `lefthook.yml` | native + governance entry points; Git hooks opt-in (`make hooks-install`; `no_auto_install`) |
| `.standards.yaml`, `.standards.lock`, `.standards-baseline.json`, `.config/` | Praetor policy, lock, catalog, labels, HISS baseline, clang-tidy lane list, named test skips (`test-skips.json`, ADR-0188), Lefthook checkpoint scripts + policy, engine launcher (`lefthook/engine.sh`) + make fragment (`praetor/engine.mk`), agent interceptor |
| `.devcontainer/` | Praetor-rendered dev container bundle (audit: byte-exact; regenerate, never hand-edit); `base/Containerfile` toolchain base image (ADR-0153) |
| `.paperclip/` | Paperclip harness pair; string edit: re-pin `register.sources` from `sh .config/lefthook/engine.sh caveman check --configured-sources --root=.` |
| `.agents/agents/` | canonical reviewer personas |
| `.claude/agents/`, `.codex/agents/`, `.github/agents/`, `.gemini/agents/` | generated persona projections |
| `.claude/skills/`, `.claude/hooks/`, `.codex/hooks/`, `.gemini/settings.json` | agent skills + hooks; see below |
| `.vscode/`, `.zed/`, `.idea/`, `.helix/`, `.fleet/`, `.nvim.lua`, `lua/`, `.dir-locals.el`, `standards.sublime-project` | reconciled editor settings |
| `.github/workflows/` | `ci.yml` product jobs, `standards-gate.yml`, locked `praetor-docs.yml`, `release.yml` (`ci` -> `build` -> tag-only `publish`), reusable `release-build.yml`: build, SBOM, SLSA L3 attestation, cosign; no artefact download or cache before attestation (ADR-0169); `devcontainer-image.yml` builds base image on PR, pushes + attests `ghcr.io/vmafx/pelorus-dev` from `master`; `windows-tester.yml` (path-filtered MSYS2 replay + tester zip artifact, no release); Praetor-rendered `reuse.yml` runs `reuse lint`; `codeql.yml` c-cpp, python, actions code scanning, contract `scripts/check-codeql-workflow.py` (ADR-0220) |
| `.github/rulesets/main.json` | rendered `master` ruleset (0 reviews, signed commits, linear history, 13 checks); committed, applied only by maintainer `praetorctl sync --remote` |

New top-level package: add row here. New module: scoped `AGENTS.md`.

## Skills and hooks

| Skill (`.claude/skills/`) | Use |
| --- | --- |
| `build` | configure, build, fast suite; local gate |
| `format-all`, `lint-all` | clang-format; clang-tidy + shader compile |
| `add-vulkan-filter` | scaffold new `vf_pelorus_*` filter end-to-end |
| `ffmpeg-build-patches`, `ffmpeg-apply-patches` | regenerate stack; apply + build + smoke |
| `new-adr` | reserve + create ADR before implementing commit |
| `bump-abi` | append-only interop ABI extension |
| `render-changelog` | render `CHANGELOG.md` from `changelog.d/` |
| `cut-release` | version bump + tag; release workflow |
| `caveman`, `social-text`, `adhd-format` | text register forms; Praetor-installed in `.agents/skills/`, projected by `compile-context` |

`.claude/skills/superpowers/`: vendored obra/superpowers process skills (verification-before-completion, systematic-debugging, test-driven-development, code review, git worktrees); template for new skills. Topic links: `docs/references.md`.

Hooks: `.claude/hooks/` wired in `.claude/settings.json`; Codex twins in `.codex/hooks/` via `.codex/hooks.json`. Praetor pre-tool row (adopted `agent-hooks`, ADR-0145) in those two files plus `.gemini/settings.json`; `adopt` owns it, merge keeps Pelorus rows.

| Event | Hook | Effect |
| --- | --- | --- |
| PreToolUse Bash | `block-unsafe-bash` | blocks `rm -rf /`, force-push to `master`, hard reset onto `origin/master`, `git clean -xf`, fork bombs |
| PreToolUse Bash (Claude, Codex); BeforeTool `^run_shell_command$` (Gemini) | `praetorctl hook <client> pre-tool` | Praetor command policy, exit 2 = deny: hook-skip flags (`--no-verify`, commit `-n`), Lefthook disable, `core.hooksPath` writes, `.git/hooks` edits, `lefthook uninstall`; skips outside governed checkout; 15 s budget |
| PostToolUse Edit/Write | `auto-format-on-edit` | clang-format on C/H |
| PostToolUse Edit/Write | `shader-lockstep-warn` | guards single `.comp.glsl` source model |
| PostToolUse Edit/Write | `docs-drift-warn` | ABI, surface, build-flag change: docs, ADR, changelog, patch reminder |
| SessionStart | `session-start` | branch + PLAN/STATE orientation; build staleness |
| Stop | `stop` | local-gate reminder while sources unverified |

## Commands

| Goal | Command |
| --- | --- |
| configure | `meson setup build` |
| build | `ninja -C build` |
| fast tests | `meson test -C build --suite=fast --print-errorlogs` |
| native gate | `make verify-native` |
| governance + native gate | `make verify-all` |
| install | `ninja -C build install` |
| fetch + verify shared series | `scripts/fetch-ffmpeg-series.sh /absolute/new-dir` (`--self-test`: planted defects) |
| regenerate patches | `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/generate.sh` |
| replay stack | `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh` |
| compile contexts | `make compile-context` |
| verify contexts | `make compile-context-verify` |
| audit baseline | `sh .config/lefthook/engine.sh audit` |
| docs gate (Node 22.12+) | `make docs-lint docs-figures` |
| licence check | `reuse lint` (reuse 6.x) |
| explicit hook install | `make hooks-install` |

## Pinned upstreams

| Component | Pin |
| --- | --- |
| FFmpeg patch base | `n9.0.2` / `946fcce07b6dcd0331c8cc609192aeff5e1924f8` from `build-config.env` |
| Shared FFmpeg fix series | VMAFx/ffmpeg-patches `v0.5.0` / `7641804bdd5e73040e4b84f63440a88f4cc20576`, tarball sha256 `ddd254a720c0432a7d5f90ca76f0a2749aa2d2ea9bec3a585087c767e9ebe064`, from `build-config.env`; hand bump, no Renovate ([ADR-0185](../adr/0185-shared-ffmpeg-fix-series.md)) |
| FFmpeg Vulkan models | `vf_gblur_vulkan.c`, `vf_nlmeans_vulkan.c`, `vf_scdet_vulkan.c` |
| AV1 grain ABI mirror | `libavutil/film_grain_params.h` |
| vmafx control plane | `libvmaf_tune`, `/v1/score`, `vmaf-mcp` |
| Praetor engine | `2d23b292417386fe8259da29690266ce331062c6` from `.github/workflows/standards-gate.yml` |
| Go for actionlint (`ci.yml` docs job) | `1.27.x`; change with `ACTIONLINT_GO_VERSION` in `scripts/check-build-config.py`; checker's Renovate manager keeps setup-go's `go` identity ([ADR-0151](../adr/0151-renovate-mirrors-checker-toolchain-pins.md)) |
