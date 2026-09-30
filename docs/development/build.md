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

## Local gate (run before pushing)

```bash
meson test -C build --suite=fast
clang-format --dry-run -Werror libpelorus/**/*.{c,h}
clang-tidy -p build libpelorus/src/*.c        # touched files clean
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
  once; a Renovate config that would split or drop the bump fails the fast
  suite. The step name carries no version for the same reason.

When bumping by hand, change the `go-version` in `.github/workflows/ci.yml` and
`ACTIONLINT_GO_VERSION` together, then run
`python3 scripts/check-build-config.py --self-test` and
`go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12`.

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
