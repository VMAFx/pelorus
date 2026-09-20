# Pelorus Praetor Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reproducible Praetor/HISS governance ratchet with canonical cross-agent contexts and Pelorus-native verification, without touching product code or corrupting the repository's shared Git hooks.

**Architecture:** Generate the pinned Praetor surface in a standalone clone so `adopt` cannot mutate the active repository's shared hooks. Reconcile generated artifacts into a Pelorus-specific contract: one canonical `AGENTS.md`, canonical reviewer personas, a native Makefile/Lefthook gate, baseline-only CI, and explicit declines for unsupported branch-ruleset and devcontainer surfaces.

**Tech Stack:** Praetor `846da5908d15b3cf5581ca6b0205cc644b249599`, Go 1.27, YAML/JSON, GNU Make, Lefthook, Meson/Ninja, Python 3, GitHub Actions.

---

### Task 1: Commit the adoption decision and establish generation safety

**Files:**
- Create: `docs/adr/0145-praetor-governance-adoption.md`
- Modify: `docs/adr/README.md`
- Create: `docs/superpowers/plans/2026-09-20-praetor-onboarding.md`

- [ ] **Step 1: Validate the decision record**

Run:

```bash
git diff --check
grep -q '\[0143\](0143-ffmpeg-9-migration.md)' docs/adr/README.md
grep -q '\[0145\](0145-praetor-governance-adoption.md)' docs/adr/README.md
```

Expected: all commands exit zero.

- [ ] **Step 2: Commit ADR and plan before generated implementation**

```bash
git add docs/adr/0145-praetor-governance-adoption.md docs/adr/README.md \
  docs/superpowers/plans/2026-09-20-praetor-onboarding.md
git commit -m "docs(adr): choose the Praetor adoption contract"
```

- [ ] **Step 3: Create a standalone generation clone**

Use a run-scoped directory outside all linked worktrees:

```bash
stage=$(mktemp -d /tmp/pelorus-praetor-adopt.XXXXXX)
git clone --no-hardlinks . "$stage/repo"
git -C "$stage/repo" switch chore/praetor-onboarding-20260920
```

Record the active repository's non-sample hook names before generation:

```bash
find "$(git rev-parse --git-common-dir)/hooks" -maxdepth 1 -type f \
  ! -name '*.sample' -printf '%f\n' | sort > "$stage/hooks.before"
```

All later `adopt` writes occur in `$stage/repo`, whose `.git/hooks` is isolated.

### Task 2: Generate the declared policy, digest lock, and baseline

**Files:**
- Create: `.standards.yaml`
- Create: `.standards.lock`
- Create: `.standards-baseline.json`
- Create: `.config/archetypes/native-gpu-systems.yaml`
- Create: `.config/archetypes/facets/security-high.yaml`
- Create: `.config/archetypes/facets/api-public.yaml`
- Create: `.config/archetypes/facets/docs-seoportal.yaml`
- Create: `.config/archetypes/facets/agent-sandboxed.yaml`
- Create: generated editor, label, Paperclip, checkpoint, and hook-support artifacts accepted by the manifest

- [ ] **Step 1: Seed explicit adoption declines before generation**

Create this manifest in the standalone clone:

```yaml
version: 1
repository:
  owner: VMAFx
  name: pelorus
  visibility: public
  description: ""
  homepage: ""
  topics: []
profiles:
  - native-gpu-systems
facets:
  - security:high
  - api:public-contract
  - docs:seo-portal
  - agent:sandboxed
adoption:
  decline:
    - branch-ruleset
    - dev-container
    - git-hooks
```

- [ ] **Step 2: Run the pinned dry-run and verify its measured debt**

```bash
/home/kilian/.cache/praetor-bin/standardsctl-846da590 adopt \
  --path "$stage/repo" \
  --profile native-gpu-systems \
  --facets security:high,api:public-contract,docs:seo-portal,agent:sandboxed \
  --lock-source-root /home/kilian/.cache/praetor-846da590 \
  --dry-run
```

Expected: 60 findings: HISS-01=30, HISS-02=1, HISS-04=29;
`branch-ruleset`, `dev-container`, and `git-hooks` are reported as explicitly
declined.

- [ ] **Step 3: Materialize the adoption surface**

Run the same command without `--dry-run`. Assert that `.standards.lock`
contains digest entries for one profile and four facets,
`.standards-baseline.json` contains 60 findings,
`.github/rulesets/main.json` does not exist, `.devcontainer` does not exist,
and neither `lefthook.yml` nor a Praetor fallback hook was generated.

- [ ] **Step 4: Prove the active repository's hooks did not change**

```bash
find "$(git rev-parse --git-common-dir)/hooks" -maxdepth 1 -type f \
  ! -name '*.sample' -printf '%f\n' | sort > "$stage/hooks.after"
diff -u "$stage/hooks.before" "$stage/hooks.after"
```

Expected: no diff. Do not copy or install the standalone clone's generated `.git/hooks`.

### Task 3: Build the canonical agent harness and persona tree

**Files:**
- Modify: `AGENTS.md`
- Replace generated: `CLAUDE.md`
- Create: `.cursor/rules/hiss-invariants.mdc`
- Create: `.github/copilot-instructions.md`
- Create: `.windsurfrules`
- Create: `.gemini/GEMINI.md`
- Create: `.codex/rules.md`
- Create: `.agents/agents/*.md`
- Reconcile generated: `.claude/agents/*.md`, `.codex/agents/*.md`, `.github/agents/*.md`, `.gemini/agents/*.md`
- Preserve and correct: `.codex/agents/*.toml`, `.codex/hooks.json`, `.codex/hooks/*.sh`

- [ ] **Step 1: Demonstrate that unmodified generated context fails**

```bash
/home/kilian/.cache/praetor-bin/standardsctl-846da590 caveman check AGENTS.md
/home/kilian/.cache/praetor-bin/standardsctl-846da590 compile-context --verify
```

Expected: FAIL on `AGENTS.md` article density, proving that generated preamble plus the prose guide is not an acceptable canonical source.

- [ ] **Step 2: Rewrite `AGENTS.md` in the internal register**

Retain every binding Pelorus invariant while expressing project facts, hard rules, directory ownership, build commands, and reviewer routing as terse tables and imperative bullets. Move explanatory prose to existing human documentation. Replace the statement that `CLAUDE.md` extends `AGENTS.md` with the HISS-16 canonical/projection contract. Keep the file under Praetor's 300-line projection budget.

- [ ] **Step 3: Promote the six Pelorus reviewer personas**

Make these canonical:

```text
.agents/agents/c-reviewer.md
.agents/agents/doc-reviewer.md
.agents/agents/ffmpeg-patch-reviewer.md
.agents/agents/interop-abi-reviewer.md
.agents/agents/pr-body-checker.md
.agents/agents/vulkan-shader-reviewer.md
```

Retain generated `repo-auditor.md` and `repo-gatekeeper.md`. Correct the FFmpeg reviewer to n9.0.2 and the shader reviewer to FFmpeg 9's single `.comp.glsl` build-time source model. Remove every claim about inline GLSL and `spirv_library`.

- [ ] **Step 4: Preserve native Codex roles and make hooks relocatable**

Keep the six `.codex/agents/*.toml` role definitions beside Praetor's Markdown projections. In `.codex/hooks.json`, replace every absolute `/home/kilian/dev/Pelorus` command with the documented git-root form:

```json
"command": "\"$(git rev-parse --show-toplevel)/.codex/hooks/block-unsafe-bash.sh\""
```

Apply the same pattern to all hook commands, retain executable scripts, and do not duplicate hooks in `.codex/config.toml`.

- [ ] **Step 5: Compile and verify every projection**

```bash
/home/kilian/.cache/praetor-bin/standardsctl-846da590 caveman check AGENTS.md
/home/kilian/.cache/praetor-bin/standardsctl-846da590 compile-context
/home/kilian/.cache/praetor-bin/standardsctl-846da590 compile-context --verify
```

Expected: all PASS; no hand-edited Markdown projection differs from its canonical source.

### Task 4: Replace generic build and hook stubs with Pelorus-native gates

**Files:**
- Modify: `Makefile`
- Create: `lefthook.yml`

- [ ] **Step 1: Confirm generated `verify-all` fails for the intended reason**

```bash
make verify-all
```

Expected: FAIL with `Project verification unavailable`, proving the generated placeholder cannot be accepted.

- [ ] **Step 2: Implement native Make targets**

Define `PRAETORCTL`, `BUILD_DIR ?= build`, and these targets:

```make
.PHONY: verify-all verify-native configure build test format-check tidy docs-check \
        compile-context compile-context-verify audit hooks-install

verify-all: compile-context-verify audit verify-native
verify-native: build test format-check tidy docs-check

configure:
	meson setup $(BUILD_DIR) --reconfigure

build: configure
	ninja -C $(BUILD_DIR)

test: build
	meson test -C $(BUILD_DIR) --suite=fast --print-errorlogs

format-check:
	clang-format --dry-run --Werror $$(find libpelorus tools -type f \( -name '*.c' -o -name '*.h' \))

tidy: build
	clang-tidy -p $(BUILD_DIR) libpelorus/src/*.c

docs-check:
	bash scripts/release/concat-changelog-fragments.sh --check

compile-context:
	$(PRAETORCTL) compile-context

compile-context-verify:
	$(PRAETORCTL) compile-context --verify

audit:
	$(PRAETORCTL) audit

hooks-install:
	command -v lefthook >/dev/null
	lefthook install
```

Make `configure` work for both a new and existing build directory by testing for `$(BUILD_DIR)/meson-private/coredata.dat` and choosing `meson setup` versus `meson setup --reconfigure`.

- [ ] **Step 3: Replace Go-only Lefthook commands**

Use `compile-context --verify` and `audit` in serial pre-commit commands. Use `make verify-all` for pre-push. Remove `gofmt`, `go vet`, `govulncheck`, Go fallback execution, post-commit state mutation, and automatic hook installation. Preserve checkpoint commands only when their scripts remain generated and verified.

- [ ] **Step 4: Verify native and governance gates**

```bash
PRAETORCTL=/home/kilian/.cache/praetor-bin/standardsctl-846da590 make verify-all
```

Expected: context verification, baseline audit, build, 22-or-more fast tests, formatting, clang-tidy, and changelog hygiene all PASS.

### Task 5: Add standards CI and correct generated branch assumptions

**Files:**
- Create: `.github/workflows/standards-gate.yml`
- Modify: `.paperclip/harness.json`
- Modify: `.config/agent/checkpoint.json`
- Modify: generated editor/task artifacts containing `main`

- [ ] **Step 1: Add the pinned baseline-only workflow**

Create a workflow triggered on every pull request and pushes to `master`, with no path filter. Use Ubuntu 26.04, checkout v7.0.1 with `fetch-depth: 0`, setup-go v7 with `go-version: '1.27'`, and install:

```bash
PRAETOR_REF=846da5908d15b3cf5581ca6b0205cc644b249599
go install "github.com/cordanaLLM/praetor/cmd/standardsctl@${PRAETOR_REF}"
echo "$(go env GOPATH)/bin" >> "$GITHUB_PATH"
```

Run `standardsctl version`, `standardsctl compile-context --verify`, and baseline-only `standardsctl audit`. Do not run `audit -base` and do not install Lefthook in CI.

- [ ] **Step 2: Replace generated `main` behavior with `master`**

Set Paperclip rebase/push/base fields and checkpoint base to `master`; set repository identity to `VMAFx/pelorus`. Search all newly generated tracked artifacts:

```bash
rg -n 'refs/(for|heads)/main|origin/main|"base": "main"|pelorus-praetor-' \
  . --hidden -g '!docs/adr/0145-*' -g '!.git/**'
```

Expected: no active generated command or repository identity remains wrong. Historical explanation in ADR/research is allowed.

- [ ] **Step 3: Prove remote sync is absent**

Require no workflow, Make target, or hook to invoke `standardsctl sync --remote`. The manifest's explicit `branch-ruleset` decline is the durable guard until upstream learns `master`.

### Task 6: Document measured adoption and render the changelog

**Files:**
- Create: `docs/research/2065-praetor-adoption-measurements.md`
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Create: `changelog.d/added/0145-praetor-governance.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Record reproducible measurements**

Document the exact engine commit, profile/facets, 60-finding breakdown, generated/declined surfaces, canonical-context and persona migration, the shared-hook side effect discovered in disposable testing, commands used, and limits. State that no product source was refactored and no remote ruleset was applied.

- [ ] **Step 2: Document contributor entry points**

Add `make verify-all`, `make compile-context`, and `make hooks-install` to contributor documentation. Mark `AGENTS.md` and `.agents/agents/*.md` as canonical and vendor Markdown as generated. Add the HISS badge/gate table only if links and labels resolve to real workflow names.

- [ ] **Step 3: Render and check the changelog**

```bash
bash scripts/release/concat-changelog-fragments.sh --write
bash scripts/release/concat-changelog-fragments.sh --check
```

Expected: both commands PASS.

### Task 7: Transfer, audit, and commit the standalone result

**Files:**
- Review: every generated or reconciled adoption artifact

- [ ] **Step 1: Commit atomic units in the standalone clone**

Commit declarations/baseline, canonical contexts/personas, native gates/CI, and docs as separate conventional commits. Do not commit `.git/hooks`, build directories, receipts, or run caches.

- [ ] **Step 2: Cherry-pick into the linked adoption branch**

Cherry-pick the standalone commits into `chore/praetor-onboarding-20260920`. Re-run context compilation in the linked worktree because generated output must not depend on the staging path.

- [ ] **Step 3: Run full final verification**

```bash
PRAETORCTL=/home/kilian/.cache/praetor-bin/standardsctl-846da590 make verify-all
/home/kilian/.cache/praetor-bin/standardsctl-846da590 compile-context --verify
/home/kilian/.cache/praetor-bin/standardsctl-846da590 audit
git diff --check chore/dependency-ci-20260920..HEAD
git status --short
```

Expected: all PASS; worktree clean; baseline remains exactly 60 or decreases only for intentional documentation/harness movement; no product C, headers, shaders, or FFmpeg patch source changed.

- [ ] **Step 4: Verify shared hook state one final time**

Compare active non-sample hooks with the pre-adoption inventory. Expected: unchanged. Hook installation is delivered as an explicit target, not performed by this implementation.
