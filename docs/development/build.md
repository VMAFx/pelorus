<!-- markdownlint-disable MD013 -->
# Building Pelorus

## libpelorus (the core)

Meson + Ninja.

```bash
meson setup build
ninja -C build
meson test -C build --suite=fast      # interop ABI + UTF-8 paths + shader-compile checks
ninja -C build install                # install lib + headers + pkg-config
```

Every libpelorus parameter that names a file is UTF-8 on every platform
([ADR-0149](../adr/0149-windows-utf8-paths.md)); see
[docs/api/interop-abi.md](../api/interop-abi.md#x265-csv-reader-the-runnable-closed-loop-adr-0122).

Options (`meson_options.txt`):

| Option | Default | Effect |
| --- | --- | --- |
| `tests` | true | build + register the libpelorus test suite |
| `shaders` | true | compile the standalone reference `.comp` shaders to SPIR-V (needs glslang) |
| `tools` | true | build the libpelorus CLI demonstrators (`pelorus_qp_report`; not installed) |

## Windows (MSYS2 UCRT64)

libpelorus and its fast suite build natively on Windows with the MSYS2 UCRT64
toolchain (MinGW-w64 GCC on the Universal CRT). CI runs this in the `windows`
job of `.github/workflows/ci.yml` on a pinned `windows-2025` image
([ADR-0149](../adr/0149-windows-utf8-paths.md)). To reproduce it locally, open
a UCRT64 shell (`C:\msys64\ucrt64.exe`) and run:

```bash
pacman -S --needed mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-meson \
  mingw-w64-ucrt-x86_64-ninja mingw-w64-ucrt-x86_64-python \
  mingw-w64-ucrt-x86_64-glslang mingw-w64-ucrt-x86_64-shaderc
git config --global core.autocrlf false   # build the same bytes as Linux
meson setup build && ninja -C build
meson test -C build --suite=fast --print-errorlogs
meson test -C build path-utf8 --verbose   # the UTF-8 path contract transcript
```

From PowerShell, set `$env:MSYSTEM='UCRT64'` and `$env:CHERE_INVOKING='1'`, then
run `C:\msys64\usr\bin\bash.exe -lc '<commands>'`.

CI installs with `update: false`: the packages come from the sync database
bundled in the MSYS2 base archive of the pinned `setup-msys2` release, not
from a fresh `pacman -Sy`. A local MSYS2 that is kept up to date can therefore
run newer packages than CI. The job's receipt step prints `pacman -Q` for the
six packages, so the two can be compared.

Windows-specific behavior of the suite:

- **`path-utf8`** asserts the Windows half of the UTF-8 path contract. It
  creates a directory + file named with Latin-1, CJK, and an astral emoji
  through `_wmkdir` and an exclusive `_wopen`, then reads them back through
  `pel_x265_csv_parse`'s UTF-8 path. It checks that ill-formed UTF-8 returns
  `PEL_ERR_INVALID` with `errno == EILSEQ`, that an over-long path returns
  `PEL_ERR_ABSENT` with `errno == ENAMETOOLONG`, and that a `\\?\` path past
  `MAX_PATH` opens. On POSIX the same test asserts the literal `fopen`
  pass-through instead. The test avoids the C11 `"wx"` open mode, which the
  legacy `msvcrt.dll` runtime rejects, so it also passes with a non-UCRT
  MinGW-w64 GCC; UCRT64 remains the toolchain CI tests.
- **Shader compile checks** discard their SPIR-V to `NUL`, not `/dev/null`: a
  MinGW `glslangValidator` would otherwise write a real `\dev\null` file.
- **`build-config-sync`** runs `scripts/check-build-config.py` without
  `--self-test` on Windows. The self-test's Git fixtures model the Linux-only
  FFmpeg replay; MSYS2 argument globbing and Python's CRLF text mode break them,
  and the Linux jobs run the full self-test on every PR.

The FFmpeg patch-stack scripts below remain Linux-only.

## FFmpeg filters (the patch stack)

Requires `libpelorus` installed (visible to `pkg-config`) plus a Vulkan loader +
SPIR-V compiler.

```bash
cd ffmpeg-patches
FFMPEG_REPO=/absolute/path/to/ffmpeg ./generate.sh           # regenerate patches
FFMPEG_REPO=/absolute/path/to/ffmpeg ./test/build-and-run.sh # apply + build + smoke
```

Both scripts read the exact tag and peeled commit from root `build-config.env`,
verify that the qualified tag resolves to that commit, and work in an isolated
run-owned Git worktree. The committed `*.patch` files are the artifact; edit
the sources under `files/` and regenerate. Patch replay supplies its own
ephemeral committer identity and neutralizes caller signing, hooks, and diff
ordering, so the same command works on a clean CI runner and a configured
developer workstation.

## Repository verification entry points

```bash
make verify-native
```

`make verify-native` is the primary product gate. It configures and builds with
Meson/Ninja, runs the fast suite, checks C formatting and clang-tidy, and checks
that `CHANGELOG.md` matches `changelog.d/`. Override `BUILD_DIR` to use another
Meson tree, for example `BUILD_DIR=build-asan make verify-native`.

The governance targets use the first `standardsctl` or `praetorctl` on `PATH`.
Pin another executable explicitly with `PRAETORCTL=/absolute/path/to/standardsctl`.
The documentation targets need Node.js 22.12 or newer; the gate installs its own
locked `markdownlint-cli2` 0.23.2 dependency tree into a temporary directory.

| Target | Composition and purpose |
| --- | --- |
| `make compile-context` | Regenerate cross-tool context and persona projections from their canonical sources |
| `make compile-context-verify` | Fail if a generated projection has drifted |
| `make audit` | Verify the pinned manifest/lock, enforce the 51-finding HISS baseline ratchet in the hosted Standards mode (`--base`, touched-debt delta; `AUDIT_BASE` overrides the base), and run the engine's policy checks (build warnings, clang-tidy coverage, supply chain, Paperclip pair) |
| `make docs-lint` | Lint public Markdown with the locked Praetor configuration and reject links into private scratch directories |
| `make docs-figures` | Check figure specs and sources; skips with a reason while `docs/figures/` has none |
| `make verify-all` | Run context verification, the audit, `verify-native`, then `docs-lint` and `docs-figures` |
| `make hooks-install` | Install the tracked Lefthook commands into the shared Git hooks directory; see the note below |

The baseline accepts 51 existing findings in the scanner's supported-file scope
(C, headers, Python, shell, JavaScript/TypeScript, and workflow files):
HISS-01=28 (`goto` cleanup jumps), HISS-04=21 (functions over 60 lines: 19 in
C, two in Python), and HISS-07=2 (`sys.exit` calls outside a `__main__` entry
point). It is a non-regression ceiling, not a claim of zero debt or whole-tree
source coverage. Fingerprints are keyed by file and line
([Praetor issue 29](https://github.com/cordanaLLM/praetor/issues/29)), so
moving a legacy function can surface it as new.

The local target and the hosted job apply that ceiling differently:

| Where | Command | Fails when |
| --- | --- | --- |
| `standardsctl audit` (plain) | `standardsctl audit` | a finding's fingerprint is not in the baseline; the total exceeds the baseline; any finding, baselined or not, sits in a file with uncommitted changes (Praetor's touched-file rule) |
| `make audit` | `standardsctl audit --base "$(AUDIT_BASE)" --touched-debt-delta-reason "$(AUDIT_DEBT_REASON)"`, base `origin/master` unless `AUDIT_BASE` is set | as hosted step 1 |
| Hosted, step 1 | `standardsctl audit --base <target> --touched-debt-delta-reason <reason>` | the committed baseline records more findings than the baseline on the target (growth guard); a file the branch touches has more findings of some rule than the committed baseline records; a finding in an untouched file is not in the baseline; the total exceeds the baseline |
| Hosted, step 2 | `standardsctl baseline --verify` | a current finding is not recorded at its current line; the total exceeds the baseline |

The target is `origin/<base branch>` for a pull request and the replaced
commit for a push to `master`. Without the debt-delta reason, `--base` would
revoke every baselined finding in a touched file; that zero-debt mode stays
deferred by ADR-0145. Step 1 accepts a touched file whose findings only moved
lines, so step 2 makes the branch re-record the baseline for them. A stale
fingerprint on `master` would otherwise fail the next pull request that does
not touch that file.

Existing findings may shrink. A deliberate increase recorded with
`standardsctl baseline --record --allow-increase --reason=<why>` passes the
growth guard with a `[WARN]` line that prints the reason, so the increase stays
visible in the job log and in the baseline diff. Any change to the baseline
total also changes the managed README block, which the audit compares with the
baseline; refresh it with the adoption command in the ADR. To run the hosted
steps locally:

```bash
CI=true standardsctl audit --base origin/master \
  --touched-debt-delta-reason "local run of the hosted ratchet"
standardsctl baseline --verify
```

Two hosted workflows run on every pull request. `Standards` installs Praetor at
the commit in [ADR-0168](../adr/0168-praetor-engine-492a00f.md), prints
the module version Go recorded for it, verifies generated contexts, and runs
the two ratchet steps above. `Standards` is not yet a required status check on
`master`; branch protection requires the `core`, `ffmpeg-stack`, and `docs`
jobs. `Praetor Documentation Governance` is Praetor's locked
workflow for the `docs:seo-portal` facet: it runs the same Markdown and figure
checks as `make docs-lint docs-figures`. On a draft pull request its first
step, `Stop on a draft pull request`, fails on purpose and the checks do not
run; they run once the pull request is marked ready for review. `praetorctl
audit` compares that
workflow, `tools/markdownlint/`, `tools/figures/`, the documentation block in
the `Makefile`, and the managed block at the end of `.gitattributes` byte for
byte with the pinned engine's assets. Refresh them only with the adoption
command in the ADR, never by hand.

The manifest declines `git-hooks`, and the audit reports that decline as a
pass, locally and in CI ([Praetor issue
175](https://github.com/cordanaLLM/praetor/issues/175), fixed before the
ADR-0168 pin). Do not create a placeholder hook file, weaken the manifest, or
run remote sync to get past a failing check.

Two audit checks read lists that ordinary code changes can break:

- **Build warnings (HISS-10).** Every `meson setup` in `.github/workflows/`
  passes `--werror`. The audit reads only the command line, not the
  `werror=true` in `meson.build`'s `default_options`, so a hosted lane without
  the flag fails even though Meson would build it with warnings as errors.
  `scripts/check-build-config.py` also pins the Windows job's
  `meson setup --werror build && ninja -C build`.
- **clang-tidy coverage.** `.standards.yaml` declares one clang-tidy lane,
  `libpelorus`, whose units are listed in `.config/clang-tidy/lane-files.txt`:
  every C file the Meson build compiles (`libpelorus/src`, `libpelorus/test`,
  `tools`, and the two `ffmpeg-patches/test` programs Meson builds against a
  stub header). `make tidy` and the `core` job's clang-tidy step read that same
  list, so the lane the audit judges is the lane that runs. A tracked C unit
  that no lane reads fails the audit unless the top-level `exceptions` list
  names it with a reason and an expiry at most 90 days ahead. Add a new Meson
  unit to the list in the same change. The 13 `ffmpeg-patches` units that
  build only inside an FFmpeg tree have one `clang-tidy-coverage` exception
  each, expiring 2027-01-06; VMAFx/pelorus#94 tracks the FFmpeg-tree lane that
  replaces them.

Triage failures by boundary:

- A `verify-native` failure belongs to Pelorus and must be fixed here.
- Context drift means a canonical source changed without regeneration. Edit
  `AGENTS.md` or `.agents/agents/*.md`, then run `make compile-context`; never
  patch a generated projection directly.
- A `docs-lint` diagnostic names a file, line, and markdownlint rule. Fix the
  Markdown; the rule set is locked by the engine.
- Any audit failure is a governance regression in this repository. Fix it,
  or, where the check allows one, declare a dated entry in the `exceptions`
  list of `.standards.yaml` in a reviewed change.

Canonical context sources are `AGENTS.md` and `.agents/agents/*.md`. Generated
files that `compile-context` owns include root `CLAUDE.md`,
`.cursor/rules/hiss-invariants.mdc`, `.github/copilot-instructions.md`,
`.windsurfrules`, `.gemini/GEMINI.md`, `.codex/rules.md`,
and the Markdown persona projections under `.claude/`, `.codex/`, `.github/`,
and `.gemini/`. Direct edits to those projections are overwritten or rejected
by verification. The text register block between the
`<!-- praetor:register:start -->` markers in `AGENTS.md` is rendered from the
manifest by `compile-context`; do not edit it by hand.

The engine installs three text-register skills (`caveman`, `social-text`,
`adhd-format`) under `.agents/skills/`; `compile-context` copies them to
`.claude/skills/`. `compile-context --verify` and the audit also lint the
nested `ffmpeg-patches/AGENTS.md`, `libpelorus/AGENTS.md`, and
`tools/AGENTS.md` in the Caveman register: at most 2.0 articles per 100 prose
words and at most 30 words per sentence. Check one file with
`praetorctl caveman check --kind=context <file>`.

`.paperclip/harness.json` and `.paperclip/rules.md` are a separate generated
pair from `standardsctl paperclip harness`; `compile-context` does not own them.
Regenerating that pair requires an explicit consumer review because the pinned
generator does not yet honor every Pelorus branch, language, and policy choice
(Praetor issue 321). The audit lints the harness's operating-contract and
invariant strings in the internal (Caveman) register. `register.sources` in
`.standards.yaml` records how many strings it read and their digest, so an
edited string also needs new pins. Since the ADR-0168 pin the audit also fails
when `rules.md` is not the engine's rendering of `harness.json`. `adopt` refuses to re-bind that drift, even
with `--force`. Stage the edit, run
`standardsctl caveman check --configured-sources --root=.`, and copy the
`actual` digest (and count, if it changed) that it reports into
`register.sources`.

### Declared policy that no gate runs

The profile and facets in `.standards.yaml` declare more controls than Pelorus
executes. The audit verifies the lock, the baseline, generated surfaces, the
documentation gate, build warnings, clang-tidy coverage, and the release
provenance, signature, and SBOM ([Release](#release)). It does not check the
controls below, and no workflow implements them; they are declared only
([ADR-0145](../adr/0145-praetor-governance-adoption.md)).

| Declared by | Control | Current state |
| --- | --- | --- |
| `native-gpu-systems`, `security:high`, `api:public-contract` | Signed commits, two approving reviews, stale-review dismissal | Not enforced; `master` protection requires linear history and three CI checks, no signatures and no reviews |
| `native-gpu-systems` | `semgrep`, `cppcheck`, `clippy` | Not run; Pelorus has no Rust for `clippy` |
| `security:high` | `gitleaks`, `trivy` | Not run; `.gitleaks.toml` only configures a manual `gitleaks` run |
| `api:public-contract` | `buf`, `spectral`, OpenAPI drift, `Migration:` footer check | Not run; Pelorus has no protobuf or OpenAPI surface. The append-only C ABI is guarded by the interop conformance fixture and review |
| `docs:seo-portal` | Schema.org JSON-LD, sitemap, `robots.txt`, Core Web Vitals | Not applicable; Pelorus builds no documentation site. The facet's locked Markdown and figure gate does run |
| `native-gpu-systems` | No per-frame dynamic allocation | HISS-03, by C review only |

The declared linters that do run are `clang-tidy` (the `core` job and
`make verify-native`, over the units in `.config/clang-tidy/lane-files.txt`)
and `markdownlint` (the documentation gate). HISS-04
complexity is split the same way. For C the audit ratchets only the 60-line
function cap. Its cyclomatic, cognitive, and statement measurement covers Go
sources only, so the effective limits of 10, 12, and 40 are not machine-checked
here. The `.clang-tidy` function-size thresholds (75 lines, 120 statements, 20
branches) are advisory and cover the clang-tidy lane only. Those limits are reviewer
checks.

### Agent hooks are registered; Git hooks stay opt-in

The manifest no longer declines `agent-hooks`
([ADR-0145](../adr/0145-praetor-governance-adoption.md)). Adoption registers
the engine's pre-tool interceptor in each agent client's tracked hook file,
beside Pelorus's own hooks:

| File | Client event and matcher | Command | Timeout |
| --- | --- | --- | --- |
| `.claude/settings.json` | `PreToolUse`, `Bash` (the group that also runs `block-unsafe-bash.sh`) | `praetorctl hook claude pre-tool` | 15 s |
| `.codex/hooks.json` | `PreToolUse`, `Bash` (same group as the Codex `block-unsafe-bash.sh`) | `praetorctl hook codex pre-tool` | 15 s |
| `.gemini/settings.json` | `BeforeTool`, `^run_shell_command$` | `praetorctl hook gemini pre-tool` | 15000 ms |

The hook reads the client's JSON payload on stdin, finds the repository from
the payload's `cwd` (or its own working directory), and judges only the shell
command. It denies with exit 2 and a `[BLOCKED BY HISS] ...` line on stderr
when the command skips Git hooks (`--no-verify` and its abbreviations, `-n` on
`git commit` or `git am`), disables Lefthook, sets `core.hooksPath`, writes
into `.git/hooks`, or runs `lefthook uninstall`. Any other command passes with
exit 0 and no output. Outside a checkout that holds `.standards.yaml` it skips
with exit 0 and a reason on stderr. Praetor's `docs/guides/agent-hooks.md` at
the pinned commit lists every rule. The hook judges agent tool calls only;
commands you type in your own terminal never pass through it.

Everyone who runs Claude Code, Codex, or Gemini CLI in this repository needs
an executable named `praetorctl` on `PATH`. The pinned `go install` builds
`standardsctl`, so copy it under the name the hooks call, then check it with a
sample payload from the repository root:

```bash
go install github.com/cordanaLLM/praetor/cmd/standardsctl@492a00f930e1a2df557ffebb76564cfa65637b77
cp "$(go env GOPATH)/bin/standardsctl" "$(go env GOPATH)/bin/praetorctl"   # Windows: standardsctl.exe to praetorctl.exe
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"ls"},"hook_event_name":"PreToolUse"}' \
  | praetorctl hook claude pre-tool; echo "exit=$?"                          # expect exit=0, no output
```

When `praetorctl` is missing, the shell answers 127 (bash) or 1 (`cmd` and
PowerShell). Claude Code and Gemini CLI treat every exit other than 2 as a
non-blocking error: they show it and run the command without the Praetor
policy. Codex documents only exit 0 and exit 2, so its handling of 127 is
unverified. Pelorus's own `block-unsafe-bash` hook runs either way. Install the
engine before starting an agent session rather than relying on that fallback.

On Windows, Claude Code runs hook commands through Git Bash, which the
`.claude/hooks/*.sh` scripts need anyway; Gemini CLI runs them through
PowerShell and appends `exit $LASTEXITCODE` itself. The exit codes above hold
under Git Bash, `cmd /c`, a direct PowerShell call, and Gemini's PowerShell
form ([research digest 0145](../research/0145-praetor-adoption-measurements.md)).
A wrapper that ends with the hook as the last statement of
`powershell -Command` without `exit $LASTEXITCODE` would turn a deny (2) into
1, which the clients do not block on.

Adoption owns the three registrations. It adds a missing row and leaves a file
that already has one byte for byte as it is, so re-running the adoption command
in the ADR is how to restore a row. Keep Pelorus's own hook entries where they
are; the merge preserves them.

The manifest still declines `git-hooks`, so adoption never installs Lefthook
into `.git/hooks`. `make hooks-install` remains available for explicit
hook-integration testing. The tracked `lefthook.yml` binds pre-commit to
`make compile-context-verify` and `make audit`, and pre-push to
`make verify-all`, so an installed hook also needs Go, the pinned engine, and
Node.js. Linked worktrees share the repository's Git hooks directory, so
installing or removing hooks is repository-wide. Remove them with Lefthook's
verified removal command, typed in your own terminal (the agent hook denies it
to agents):

```bash
lefthook uninstall
```

## Dependency updates (Renovate)

`renovate.json` drives two kinds of machine updates, and the build-config
checker (`meson test -C build --suite=fast`, test `build-config-sync`) fails if
either would leave a copied value behind:

- **FFmpeg pin** — one regex manager updates `FFMPEG_TAG` and `FFMPEG_COMMIT`
  in `build-config.env` together
  ([ADR-0144](../adr/0144-ffmpeg-pin-and-ci-runner-policy.md)).
- **actionlint Go toolchain** — the docs job's `actions/setup-go` step
  (`go-version: '<major>.<minor>.x'`) is bumped by Renovate's built-in
  github-actions handling as dependency `go` (datasource `github-releases`,
  package `actions/go-versions`, `npm` versioning). The checker's own expectation,
  `ACTIONLINT_GO_VERSION` in `scripts/check-build-config.py`, is covered by a
  second regex manager with exactly those templates, so both edits share one
  `renovate/go-<major>.x` branch and PR. The checker verifies that manager's
  templates and that its `matchStrings` entry captures the literal exactly
  once. Removing or breaking that manager fails the fast suite. The checker
  does not evaluate other `renovate.json` settings (`ignorePaths`,
  `enabledManagers`, `packageRules`, `extends` presets); if one of them splits
  or drops the bump, the Renovate PR that edits only one site fails
  `build-config-sync`, and that red PR is the detection point
  ([ADR-0152](../adr/0152-renovate-guard-enforcement-scope.md)). The step name
  carries no version for the same reason
  ([ADR-0151](../adr/0151-renovate-mirrors-checker-toolchain-pins.md)).

The Windows job's `msys2/setup-msys2` action is bumped by Renovate's
github-actions manager like every other action (digest plus `# vX.Y.Z`
comment). The checker holds no copy of that pin. It checks only its shape:
exactly one `uses: msys2/setup-msys2@<40-hex digest> # vX.Y.Z` line, before
the first MSYS2 step. So such a bump changes `ci.yml` alone and stays green
([ADR-0149](../adr/0149-windows-utf8-paths.md)).

The checker decides which managers cover a file the way Renovate does, through
`managerFilePatterns` (see
[research digest 0151](../research/0151-renovate-setup-go-mirroring.md)). An
entry is either a `/regex/` or `/regex/i`, optionally negated with `!`, or a
minimatch glob with `dot` and `nocase`. Globs may use `*`, `?`, whole-segment
`**`, `{a,b}` and a leading `!`. The checker reports any other glob syntax as an
error rather than guess at it: character classes, extglobs, escapes, ranges,
and braces that span `/`. Use a `/regex/` entry for anything more specific.

When bumping by hand, change the `go-version` in `.github/workflows/ci.yml` and
`ACTIONLINT_GO_VERSION` together, then run
`python3 scripts/check-build-config.py --self-test` and
`go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12`.

## Release

SemVer tags `v<major>.<minor>.<patch>`. The interop ABI is append-only from
v0.1.0 (`PELORUS_ABI_MINOR` bumps on additions). The shared conformance fixture
must pass in both Pelorus and vmafx before a release that touches the ABI.

The `Release` workflow (`.github/workflows/release.yml`) runs three jobs in
order ([ADR-0169](../adr/0169-release-provenance-slsa3.md)):

1. `ci` calls the whole `CI` workflow (`uses: ./.github/workflows/ci.yml`:
   core, FFmpeg patch-stack regenerate, replay, link and smoke, sanitizers,
   Windows, docs), so a tagged commit with a broken stack never builds a
   release. The call runs with the caller's `github` context, and `ci.yml`
   keys its concurrency group on the workflow name so it cannot cancel a push
   or pull-request run.
2. `build` (`needs: ci`) calls the reusable `.github/workflows/release-build.yml`,
   whose only trigger is `workflow_call`. Its one job configures with
   `meson setup --werror build`, builds with `make build`, runs the fast suite,
   checks the rendered changelog, extracts the release notes, and packages
   `pelorus-ffmpeg-patches-<tag>.tar.gz` (`series.txt`, the README, the
   numbered patches, and `files/`). It then writes an SPDX JSON SBOM of that
   archive, writes `SHA256SUMS` over the archive and the SBOM, attests SLSA v1.0
   build provenance for both with `actions/attest-build-provenance`, signs
   `SHA256SUMS` keyless with cosign into `SHA256SUMS.sigstore.json`, verifies
   that signature against its own workflow identity, and uploads the files as
   one workflow artefact. No step downloads an artefact or restores a cache
   before the attestation, so the attestation covers only what this job built.
   GitHub signs it with the reusable workflow's identity, isolated from the
   caller: SLSA Build Level 3, which `praetorctl audit` measures (HISS-11).
3. `publish` (`needs: build`) runs only on a `v*` tag push. It downloads the
   artefact, re-checks `SHA256SUMS`, and runs `gh release create` with the
   archive, the SBOM, the provenance bundle, `SHA256SUMS`, and
   `SHA256SUMS.sigstore.json`. It is the only job with `contents: write`.

A tag push also asserts that `${GITHUB_REF_NAME#v}` equals the version meson
reports (`meson introspect --projectinfo build`; meson already fails
configuration if `pelorus.h` disagrees) and fails with an `::error::` line on a
mismatch. A manual `workflow_dispatch` is a **non-publishing rehearsal**: it
runs `ci` and `build`, including the attestation and the signature, skips only
the tag assertion, and never runs `publish`. Review the tag and the rendered
`[Unreleased]` notes before pushing a tag: a manual dispatch is not a
substitute for the tag event and never publishes on its own.

`scripts/check-build-config.py` enforces this shape: the CI call and the
`needs` chain, `workflow_call` as the only trigger of `release-build.yml`, the
tag step, the build-before-attestation step order, no artefact download or
cache in the release build, the SBOM action's upload switches, the signer
identity of the verification, the tag-only `publish` job, its asset list, and
`contents: write` on `publish` alone. Its self-test rejects a mutation of each.

### Verifying a release

Each release carries five files besides the notes. Verify them with the GitHub
CLI, cosign v3 or newer, and `sha256sum`:

```bash
TAG=v0.2.3
gh release download "$TAG" --repo VMAFx/pelorus
# SLSA provenance: signed by the reusable release build, built from the tag
gh attestation verify "pelorus-ffmpeg-patches-$TAG.tar.gz" -R VMAFx/pelorus \
  --signer-workflow VMAFx/pelorus/.github/workflows/release-build.yml \
  --source-ref "refs/tags/$TAG"
# cosign signature over the checksums
cosign verify-blob \
  --certificate-identity "https://github.com/VMAFx/pelorus/.github/workflows/release-build.yml@refs/tags/$TAG" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --bundle SHA256SUMS.sigstore.json \
  SHA256SUMS
# the archive and the SBOM match the signed checksums
sha256sum -c SHA256SUMS
```

`gh attestation verify` fetches the attestation from GitHub; pass
`--bundle "pelorus-ffmpeg-patches-$TAG.provenance.sigstore.json"` to verify
against the bundle attached to the release instead. Because a reusable
workflow signs the attestation, `--signer-workflow` names
`release-build.yml`, not `release.yml`. The SBOM,
`pelorus-ffmpeg-patches-<tag>.spdx.json`, has no signature of its own:
`SHA256SUMS` covers it, and the provenance names it as a subject.
