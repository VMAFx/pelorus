# Pelorus Dependency and CI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans` and
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Move the supported FFmpeg baseline from n9.0.1 to n9.0.2, pin the
tag and its peeled commit in one machine-maintained contract, and restore all
Pelorus CI lanes on Ubuntu 26.04 without LunarG's retired apt repository.

**Architecture:** `build-config.env` owns the FFmpeg remote, release tag, and
immutable commit. Generation and replay source it, verify tag-to-commit
identity, and create private run-scoped worktrees. A fast structural checker
proves that operational consumers use the variables instead of copying a
version. GitHub Actions uses Ubuntu Resolute's Vulkan/SPIR-V packages and the
same replay script used locally.

**Non-goals:** Do not bump Pelorus's project/release version, change public ABI,
redesign filters, apply a remote branch ruleset, or claim hosted CI acceptance
from local tests.

**Ordering constraint:** The primary checkout contains active canonical
filter/shader fixes. Validate and commit those fixes on an intermediate branch,
then rebase this branch onto that commit before regenerating. Do not generate
n9.0.2 artifacts from the old canonical inputs and later merge newer inputs on
top.

---

### Task 1: Land the decision and research record before implementation

**Files:**
- Create: `docs/adr/0144-ffmpeg-pin-and-ci-runner-policy.md`
- Create: `docs/research/0144-ffmpeg-9.0.2-ci-refresh.md`
- Modify: `docs/adr/README.md`
- Modify: `docs/superpowers/plans/2026-09-20-dependency-ci-refresh.md`

- [x] **Step 1: Record the decision**

Accept a three-value FFmpeg pin (`remote`, `tag`, peeled `commit`), safe
run-scoped worktrees, Ubuntu 26.04 native packages, and an explicit hosted-run
acceptance gate. Include an alternatives matrix covering tag-only pins,
Ubuntu 24.04, and continued LunarG use. Cite `req`, ADR-0104, ADR-0108, and
ADR-0143.

- [x] **Step 2: Record primary-source evidence and limits**

The research digest must capture:

- FFmpeg n9.0.2's release and peeled commit
  `946fcce07b6dcd0331c8cc609192aeff5e1924f8`;
- Ubuntu Resolute package availability for `libvulkan-dev`, `glslc`, and
  `glslang-tools`;
- LunarG's apt-package retirement;
- GitHub's Ubuntu 26.04 preview/SLA caveat;
- Renovate regex-manager and validator syntax;
- the difference between local verification and hosted-runner acceptance.

- [x] **Step 3: Commit decision artifacts before implementation**

```bash
git add docs/adr/0144-ffmpeg-pin-and-ci-runner-policy.md \
  docs/research/0144-ffmpeg-9.0.2-ci-refresh.md docs/adr/README.md \
  docs/superpowers/plans/2026-09-20-dependency-ci-refresh.md
git commit -m "docs(adr): pin the FFmpeg and CI baseline"
```

### Task 2: Establish a tested, immutable dependency contract

**Files:**
- Create: `build-config.env`
- Create: `scripts/check-build-config.py`
- Modify: `meson.build`
- Modify: `renovate.json`

- [x] **Step 1: Register a failing fast test**

Register `build-config-sync` beside the binding-order test using the existing
`python_prog` and source-root idiom. Initially test only the configuration
schema, so later tasks can extend consumer checks without an impossible
intermediate green state.

The checker must parse simple assignments without executing the file and
require exactly:

```text
FFMPEG_REMOTE=https://github.com/FFmpeg/FFmpeg.git
FFMPEG_TAG=n9.0.2
FFMPEG_COMMIT=946fcce07b6dcd0331c8cc609192aeff5e1924f8
```

Validate the tag shape (`nMAJOR.MINOR.PATCH`) and a lowercase 40-hex commit,
but do not duplicate today's tag or commit inside the checker.

- [x] **Step 2: Confirm RED, then add the contract**

```bash
meson setup --reconfigure build
meson test -C build build-config-sync --print-errorlogs
```

Add `build-config.env` with one Renovate marker spanning both tag and digest:

```bash
# renovate: datasource=github-tags depName=FFmpeg/FFmpeg
FFMPEG_REMOTE=https://github.com/FFmpeg/FFmpeg.git
FFMPEG_TAG=n9.0.2
FFMPEG_COMMIT=946fcce07b6dcd0331c8cc609192aeff5e1924f8
```

Configure one regex custom manager that captures `currentValue` and
`currentDigest` from the same block, with regex versioning for the leading
`n`. Validate the repository config with a pinned Renovate release:

```bash
npx --yes --package renovate@44.103.6 -- \
  renovate-config-validator --strict
```

- [x] **Step 3: Confirm GREEN and commit**

```bash
meson test -C build build-config-sync --print-errorlogs
git add build-config.env scripts/check-build-config.py meson.build renovate.json
git commit -m "build: centralize the immutable FFmpeg baseline"
```

### Task 3: Make generation and replay safe consumers of the contract

**Files:**
- Modify: `scripts/check-build-config.py`
- Modify: `ffmpeg-patches/generate.sh`
- Modify: `ffmpeg-patches/test/build-and-run.sh`

- [ ] **Step 1: Extend the checker, then confirm RED**

Require both scripts to source `build-config.env`, use `FFMPEG_COMMIT`, and
contain neither a literal FFmpeg release tag nor `/home/kilian/`. Reject
force-removal of caller paths. The check is structural: it proves variable
consumption, not one particular current version.

- [ ] **Step 2: Make worktree creation non-destructive**

Both scripts must:

1. require an explicit `FFMPEG_REPO`;
2. verify `FFMPEG_TAG^{commit}` equals `FFMPEG_COMMIT` in that checkout;
3. use `FFMPEG_COMMIT` as the detached base;
4. create a private directory with `mktemp -d` when `WORKTREE` is unset;
5. refuse an existing caller-supplied `WORKTREE`;
6. install an `EXIT` trap that aborts any `git am`, removes only the worktree
   it created, and removes only its own empty scratch parent;
7. never use `git worktree remove --force` to clear an unknown path.

- [ ] **Step 3: Make replay test the current libpelorus tree**

`build-and-run.sh` must configure, build, and install the current Pelorus tree
into a private temporary prefix. Export that prefix through `PKG_CONFIG_PATH`
and the platform runtime-library path before configuring FFmpeg. Remove
`--enable-libshaderc`, `--disable-programs`, and the single-object shortcut;
configure with `--enable-vulkan --disable-doc`, link `ffmpeg`, and verify all
ten Pelorus filters plus `pelorus_fgs` are registered.

- [ ] **Step 4: Run focused tests and replay**

```bash
meson test -C build build-config-sync --print-errorlogs
FFMPEG_REPO=/home/kilian/dev/upstream/ffmpeg-9 \
  ffmpeg-patches/test/build-and-run.sh
```

Expected: 18 patches apply, the local libpelorus prefix is reported, FFmpeg
links, and all expected filters/BSF register. Commit as:

```bash
git add scripts/check-build-config.py ffmpeg-patches/generate.sh \
  ffmpeg-patches/test/build-and-run.sh
git commit -m "build(ffmpeg): make stack replay pinned and isolated"
```

### Task 4: Move GitHub Actions to Ubuntu 26.04 and native Vulkan packages

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `scripts/check-build-config.py`

- [ ] **Step 1: Extend runner/toolchain checks, then confirm RED**

Parse every job in both workflow files and require `runs-on: ubuntu-26.04`.
Reject `ubuntu-latest`, `packages.lunarg.com`, `vulkan-sdk`, copied FFmpeg
release literals, and workstation paths. Require each build/test lane to
install both `glslc` and `glslang-tools`, and require the FFmpeg lane to install
`libvulkan-dev`.

- [ ] **Step 2: Replace LunarG with Resolute packages**

Pin all jobs to `ubuntu-26.04`. Install native `libvulkan-dev`, `glslc`, and
`glslang-tools` where appropriate. Add a `Load build configuration` step after
checkout that appends all three FFmpeg values to `$GITHUB_ENV`. Fetch the tag,
verify the peeled commit, and use the shared scripts for regeneration and
replay rather than duplicating their logic.

Each workflow must print `ImageOS`, `ImageVersion`, `/etc/os-release`, and the
installed Vulkan/shader compiler versions so the hosted acceptance run is an
auditable receipt.

- [ ] **Step 3: Make release gating testable without publishing**

Add `workflow_dispatch` to the release workflow. Run build/test/package gates
for both tag pushes and manual dispatches, but guard the GitHub release publish
step so it executes only for a `push` of a `v*` tag. Manual validation must not
create a release.

- [ ] **Step 4: Add workflow syntax validation**

Run `actionlint` v1.7.12 via an explicitly configured Go 1.26 toolchain in the
docs job (pin `actions/setup-go` by commit). Also run it locally:

```bash
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
```

- [ ] **Step 5: Confirm GREEN and commit**

```bash
python3 scripts/check-build-config.py
meson test -C build --suite=fast --print-errorlogs
git add .github/workflows/ci.yml .github/workflows/release.yml \
  scripts/check-build-config.py
git commit -m "ci: move the gate to Ubuntu 26.04"
```

Local green proves workflow structure and commands. Hosted acceptance remains
pending until every PR-triggered job and the manual release gate pass on a
GitHub-hosted runner; record `ImageVersion` and package versions from that run.

### Task 5: Integrate canonical fixes, regenerate, and synchronize surfaces

**Prerequisite:** The active canonical filter/shader fixes have passed their
own review and gates and exist as a commit. Rebase this branch onto that commit
before this task.

**Files:**
- Modify: generated `ffmpeg-patches/0001-*.patch` through `0018-*.patch` as produced
- Modify: `ffmpeg-patches/series.txt`
- Modify: `ffmpeg-patches/.commit-msg-*.txt` where claims are stale
- Modify: `README.md`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`
- Modify: `ffmpeg-patches/README.md`, `ffmpeg-patches/AGENTS.md`
- Modify: `docs/development/build.md`, `docs/backends/vulkan.md`, `docs/rebase-notes.md`
- Modify: `.github/ISSUE_TEMPLATE/bug_report.yml`, `.github/PULL_REQUEST_TEMPLATE.md`
- Modify: relevant `.claude/skills/*/SKILL.md` and `.claude/agents/*.md`
- Modify: `.gitignore`
- Create: `changelog.d/changed/0144-ffmpeg-9.0.2.md`

- [ ] **Step 1: Regenerate from the integrated canonical sources**

Use an explicit FFmpeg checkout whose n9.0.2 tag peels to the configured
commit:

```bash
FFMPEG_REPO=/home/kilian/dev/upstream/ffmpeg-9 \
  ffmpeg-patches/generate.sh
```

Review every generated patch. Any canonical source differences in this branch
must come from the reviewed prerequisite commit, not from regeneration.

- [ ] **Step 2: Update current operational claims only**

Replace n9.0.1/n8.1.1 where text describes the supported baseline or a current
command. Preserve historical release notes and benchmark environments. Update
commit-message templates from obsolete `libpelorus >= 0.1.0` claims. Add the
n9.0.2 rebase receipt, ADR-0143/0144 index rows, the AGENTS invariant, research
digest, changelog fragment, and an accurate PR reproducer/checklist. Ignore
run-scoped `.workingdir2/` evidence without ignoring tracked deliverables.

- [ ] **Step 3: Prove generation is byte-stable**

After the first regeneration, hash or stage the generated patch set. Run the
generator again from a fresh worktree and compare the second output with that
first snapshot—not with the old n9.0.1 artifacts.

- [ ] **Step 4: Render and commit**

```bash
bash scripts/release/concat-changelog-fragments.sh --write
git add .gitignore AGENTS.md CLAUDE.md CONTRIBUTING.md README.md CHANGELOG.md \
  changelog.d docs ffmpeg-patches .github .claude
git commit -m "docs(ffmpeg): declare n9.0.2 as the supported baseline"
```

### Task 6: Review and run clean final gates

- [ ] **Step 1: Reproduce all local lanes from clean directories**

Run:

```bash
meson setup build-final
ninja -C build-final
meson test -C build-final --suite=fast --print-errorlogs
CC=clang meson setup build-san-final -Db_sanitize=address,undefined \
  -Db_lundef=false -Dc_args=-fno-sanitize-recover=alignment
meson test -C build-san-final --suite=fast --print-errorlogs
bash scripts/release/concat-changelog-fragments.sh --check
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
```

Run the repository's format/tidy commands, ADR-index check, deterministic
regeneration, and a fresh full n9.0.2 replay against the temporary Pelorus
install. Retain commands, versions, results, and limitations under an ignored
run-scoped `.workingdir2/` evidence directory.

- [ ] **Step 2: Review branch scope**

```bash
git diff --check <integrated-base>..HEAD
git diff --stat <integrated-base>..HEAD
git status --short
```

Require no unreviewed canonical source changes, no release-version bump, no
workstation paths, and a clean worktree. Run the FFmpeg-patch and documentation
specialist reviews and resolve every Important/Critical finding.

- [ ] **Step 3: Separate local completion from hosted acceptance**

Do not call CI “restored” until the GitHub-hosted Ubuntu 26.04 run passes every
PR job and a manual non-publishing release run. If no branch is pushed in this
task, hand off that single external acceptance item explicitly.
