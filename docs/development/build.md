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
The documentation targets need Node.js 22 or newer; the gate installs its own
locked `markdownlint-cli2` 0.23.2 dependency tree into a temporary directory.

| Target | Composition and purpose |
| --- | --- |
| `make compile-context` | Regenerate cross-tool context and persona projections from their canonical sources |
| `make compile-context-verify` | Fail if a generated projection has drifted |
| `make audit` | Verify the pinned manifest/lock and enforce the 75-finding HISS baseline ratchet |
| `make docs-lint` | Lint public Markdown with the locked Praetor configuration and reject links into private scratch directories |
| `make docs-figures` | Check figure specs and sources; skips with a reason while `docs/figures/` has none |
| `make verify-all` | Run context verification, the audit, `verify-native`, then `docs-lint` and `docs-figures` |
| `make hooks-install` | Install the tracked Lefthook commands into the shared Git hooks directory; see the note below |

The baseline accepts 75 existing findings in the scanner's supported-file scope
(C, headers, and Python): HISS-01=31 (`goto` cleanup jumps), HISS-02=1,
HISS-04=41 (functions over 60 lines), and HISS-07=2 (`sys.exit` outside a
`__main__` entry point). It is a non-regression ceiling, not a claim of zero
debt or whole-tree source coverage. Existing findings may shrink; a finding
whose fingerprint is not in the baseline fails the audit, as does a total above
75. Praetor's touched-file rule also fails any finding in a file with
uncommitted changes. The hosted job audits a clean checkout without `--base`,
so it applies only the fingerprint and count checks; touched-file enforcement
over a pull request's range stays deferred by ADR-0145. Fingerprints are keyed
by file and line
([Praetor issue 29](https://github.com/cordanaLLM/praetor/issues/29)), so
moving a legacy function can surface it as new.

Two hosted workflows run on every pull request. `Standards` installs Praetor at
the commit in [ADR-0145](../adr/0145-praetor-governance-adoption.md), prints
the module version Go recorded for it, verifies generated contexts, and runs
the baseline audit. `Praetor Documentation Governance` is Praetor's locked
workflow for the `docs:seo-portal` facet: it runs the same Markdown and figure
checks as `make docs-lint docs-figures`. `praetorctl audit` compares that
workflow, `tools/markdownlint/`, `tools/figures/`, the documentation block in
the `Makefile`, and the managed block at the end of `.gitattributes` byte for
byte with the pinned engine's assets. Refresh them only with the adoption
command in the ADR, never by hand.

Outside CI the audit ends with one known failure: `Pre-commit hook
.git/hooks/pre-commit is missing or inactive`. The manifest declines
`git-hooks`, but the auditor ignores that decline
([Praetor issue 175](https://github.com/cordanaLLM/praetor/issues/175)).
Hosted runners set `CI=true`, and the auditor then skips only that check. Do
not create a placeholder hook file, weaken the manifest, or run remote sync to
get past it.

Triage failures by boundary:

- A `verify-native` failure belongs to Pelorus and must be fixed here.
- Context drift means a canonical source changed without regeneration. Edit
  `AGENTS.md` or `.agents/agents/*.md`, then run `make compile-context`; never
  patch a generated projection directly.
- A `docs-lint` diagnostic names a file, line, and markdownlint rule. Fix the
  Markdown; the rule set is locked by the engine.
- Any audit failure before the final pre-commit-hook diagnostic is a local
  governance regression. The hook diagnostic alone, outside CI, is the known
  upstream issue 175.

Canonical context sources are `AGENTS.md` and `.agents/agents/*.md`. Generated
files that `compile-context` owns include root `CLAUDE.md`,
`.cursor/rules/hiss-invariants.mdc`, `.github/copilot-instructions.md`,
`.windsurfrules`, `.gemini/GEMINI.md`, `.codex/rules.md`,
and the Markdown persona projections under `.claude/`, `.codex/`, `.github/`,
and `.gemini/`. Direct edits to those projections are overwritten or rejected
by verification. The text register block between the
`<!-- praetor:register:start -->` markers in `AGENTS.md` is rendered from the
manifest by `compile-context`; do not edit it by hand.

`.paperclip/harness.json` and `.paperclip/rules.md` are a separate generated
pair from `standardsctl paperclip harness`; `compile-context` does not own them.
Regenerating that pair requires an explicit consumer review because the pinned
generator does not yet honor every Pelorus branch, language, and policy choice
(Praetor issue 321). The audit lints the harness's operating-contract and
invariant strings in the internal (Caveman) register. `register.sources` in
`.standards.yaml` records how many strings it read and their digest, so an
edited string needs a matching manifest refresh through `adopt`.

### Git and agent hooks stay opt-in

The manifest declines `git-hooks` and `agent-hooks`. Adoption therefore neither
installs Lefthook into `.git/hooks` nor registers `praetorctl hook <client>
pre-tool` in `.claude/settings.json`, `.codex/hooks.json`, or
`.gemini/settings.json`. That registration would run an engine binary that a
contributor may not have installed on every agent tool call.

`make hooks-install` remains available for explicit hook-integration testing.
The tracked `lefthook.yml` binds pre-commit to `make compile-context-verify` and
`make audit`, and pre-push to `make verify-all`, so an installed hook also needs
Go, the pinned engine, and Node.js. Linked worktrees share the repository's Git
hooks directory, so installing or removing hooks is repository-wide. Remove them
with Lefthook's verified removal command:

```bash
lefthook uninstall
```

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
