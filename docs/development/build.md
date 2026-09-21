<!-- markdownlint-disable MD013 -->
# Building Pelorus

## libpelorus (the core)

Meson + Ninja.

```bash
meson setup build
ninja -C build
meson test -C build --suite=fast      # interop ABI + shader-compile checks
ninja -C build install                # install lib + headers + pkg-config
```

Options (`meson_options.txt`):

| Option | Default | Effect |
| --- | --- | --- |
| `tests` | true | build + register the libpelorus test suite |
| `shaders` | true | compile the standalone reference `.comp` shaders to SPIR-V (needs glslang) |
| `tools` | true | build the libpelorus CLI demonstrators (`pelorus_qp_report`; not installed) |

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
the sources under `files/` and regenerate.

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

| Target | Composition and purpose |
| --- | --- |
| `make compile-context` | Regenerate cross-tool context and persona projections from their canonical sources |
| `make compile-context-verify` | Fail if a generated projection has drifted |
| `make audit` | Verify the pinned manifest/lock and enforce the 78-finding HISS baseline ratchet |
| `make verify-all` | Run context verification, then the audit, then `verify-native` |
| `make hooks-install` | Install the tracked Lefthook commands into the shared Git hooks directory; see the warning below |

The baseline accepts 78 existing findings (HISS-01=31, HISS-02=1, HISS-04=46)
in the scanner's supported-file scope. It is a non-regression ceiling, not a
claim of zero debt or whole-tree source coverage. Existing findings may shrink;
new findings must not make the measured total exceed the committed baseline.

The `Standards` GitHub Actions workflow runs on every pull request and push to
`master`. It installs Praetor at the commit in
[ADR-0145](../adr/0145-praetor-governance-adoption.md), verifies generated
contexts, and runs the baseline audit. The pinned auditor currently passes the
manifest, lock, baseline, contexts, and personas, then incorrectly requires a
ruleset that the manifest explicitly declines. Therefore the Standards job,
`make audit`, and `make verify-all` are expected to stop at
[Praetor issue 408](https://github.com/CordanaLLM/praetor/issues/408). Run
`make verify-native` separately for product acceptance; do not add a fake
ruleset, weaken the manifest, or run remote sync as a workaround.

Triage failures by boundary:

- A `verify-native` failure belongs to Pelorus and must be fixed here.
- Context drift means a canonical source changed without regeneration. Edit
  `AGENTS.md` or `.agents/agents/*.md`, then run `make compile-context`; never
  patch a generated projection directly.
- An audit failure before the final missing-ruleset diagnostic is a new local
  governance regression. The final missing-ruleset diagnostic alone is the
  known upstream issue 408 blocker.

Canonical context sources are `AGENTS.md` and `.agents/agents/*.md`. Generated
files that `compile-context` owns include root `CLAUDE.md`,
`.cursor/rules/hiss-invariants.mdc`, `.github/copilot-instructions.md`,
`.windsurfrules`, `.gemini/GEMINI.md`, `.codex/rules.md`,
and the Markdown persona projections under `.claude/`, `.codex/`, `.github/`,
and `.gemini/`. Direct edits to those projections are overwritten or rejected
by verification.

`.paperclip/harness.json` and `.paperclip/rules.md` are a separate generated
pair from `standardsctl paperclip harness`; `compile-context` does not own them.
Regenerating that pair requires an explicit consumer review because the pinned
generator does not yet honor every Pelorus branch, language, and policy choice
(Praetor issues 321 and 68).

### Lefthook warning while issue 408 is open

Do **not** run `make hooks-install` for normal development while issue 408 is
open. The tracked `lefthook.yml` binds pre-commit to `make audit` and pre-push to
`make verify-all`; both reach the known auditor defect and therefore block every
commit or push even when Pelorus itself is clean. The target exists for explicit
hook-integration testing and for use after the upstream fix is pinned.

If it was installed, remove the managed hooks with Lefthook's verified removal
command:

```bash
lefthook uninstall
```

Linked worktrees share the repository's Git hooks directory, so uninstalling is
repository-wide. The tracked `lefthook.yml` remains in the checkout.

## Release

SemVer tags `v<major>.<minor>.<patch>`. The interop ABI is append-only from
v0.1.0 (`PELORUS_ABI_MINOR` bumps on additions). The shared conformance fixture
must pass in both Pelorus and vmafx before a release that touches the ABI.

The `Release` workflow has two intentionally different entry points:

- A manual `workflow_dispatch` is a **non-publishing rehearsal**. It runs the
  build/fast-test gate, checks the rendered changelog, extracts release notes,
  and constructs the FFmpeg patch-stack archive in the runner, but it cannot run
  `gh release create` and retains no published release artifact.
- Pushing a `v*` tag runs the same gate and packaging, then publishes the GitHub
  release and attaches `pelorus-ffmpeg-patches-<tag>.tar.gz`. Review the tag and
  rendered `[Unreleased]` notes before pushing: a manual dispatch is not a
  substitute for the tag event and never publishes on its own.
