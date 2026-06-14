<!-- markdownlint-disable MD041 -->
## What & why

<!-- One paragraph: what changed and the motivation. Link the ADR. -->

## Reproducer / smoke-test

<!-- A runnable command exercising the change (deep-dive deliverable #4):
     `meson test -C build --suite=fast`, an `ffmpeg -vf …` line, or
     `ffmpeg-patches/test/build-and-run.sh`. -->

```sh

```

## Per-PR checklist (ADR-0100 / ADR-0108 — see CONTRIBUTING.md)

- [ ] **ADR** for any non-trivial decision, indexed in `docs/adr/README.md` (or n/a: bugfix/impl detail)
- [ ] **Per-surface docs** under `docs/` for every user-discoverable surface (or n/a: internal/test-only)
- [ ] **Research digest** `docs/research/NNNN-*.md` (or "no digest needed: trivial")
- [ ] **Decision matrix** = the ADR's Alternatives table (or "no alternatives: only-one-way fix")
- [ ] **AGENTS.md invariant note** in the touched package (or "no rebase-sensitive invariants")
- [ ] **Changelog fragment** `changelog.d/<section>/*.md`; `concat-changelog-fragments.sh --check` passes
- [ ] **Rebase note** `docs/rebase-notes.md` if the FFmpeg patch stack is affected (or "no rebase impact: REASON")
- [ ] **Patch-stack sync**: a libpelorus surface the patches consume changed ⇒ regenerated patch is included (`/ffmpeg-build-patches`)
- [ ] **Interop ABI** (if touched) is **append-only**, `PELORUS_ABI_MINOR` bumped, conformance fixture extended (`/bump-abi`)
- [ ] **Shader lockstep**: `.comp` and the filter's inline GLSL edited together
- [ ] Touched files **lint-clean** (clang-format + clang-tidy); any `// NOLINT` cited
- [ ] Conventional Commit subject; not committing to `master` directly
