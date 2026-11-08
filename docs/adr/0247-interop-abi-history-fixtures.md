<!-- markdownlint-disable MD013 -->
# ADR-0247: Persisted interop blobs from released ABI writers and readers

- **Status**: Accepted
- **Date**: 2026-11-08
- **Deciders**: Lusoris
- **Tags**: interop, abi, test, ci

## Context

The interop side-data ABI (ADR-0103) promises that a blob written at one minor version stays readable by a reader built at any other minor of the same major: an older reader ignores section bits it does not know (R3) and reads only the prefix of a grown section that it knows (R4). The conformance test (`libpelorus/test/interop_test.c`, `test_forward_compat`) checks R4 in one process with the current headers only, by passing a smaller consumer size to the current reader. No byte sequence that a released writer produced is kept in the repository, and no released reader is ever run. A change that alters the wire bytes of an existing section, or a reader change that rejects a blob an older release wrote, passes every current test as long as writer and reader change together.

Four minors have shipped in tags: 1.0 (`v0.1.0`), 1.3 (`v0.2.0` to `v0.3.0`), 1.4 (`v0.4.0-rc.1`) and 1.5 (`v0.4.0-rc.2` to `v0.4.1`, including the zero-free carrier form of ADR-0183). Minor 1.6 (ADR-0214) is on `master` and in no tag yet. Minors 1.1 and 1.2 never reached a tag. Each tag's interop code depends only on the C standard library (`libpelorus/AGENTS.md`), so the writer and reader of any tag can be rebuilt from `git archive` without the rest of that tag's tree.

VMAFx mirrors `interop_test.c` byte for byte, so the new checks cannot live in that file without forcing a matching change in VMAFx.

## Decision

We will keep one test program, `libpelorus/test/abi_history/abi_history.c`, written once against the current tree and gated on `PELORUS_ABI_MINOR` so it compiles unchanged against every released tag's `interop.h`, `pelorus.h` and `interop.c`. Built against a tag, it is that release's writer (it packs every section and appended map the minor defines from fixed values) and that release's reader (it checks every section it knows against the same values, including the R3 and R4 rules and the carrier form). The bytes each released writer emits are persisted under `libpelorus/test/fixtures/interop-history/`, one file per minor and form, with a manifest naming the tags that produce them. The fast suite runs the current reader over every persisted blob and fails when the current writer's output differs from the fixture for the current minor or that fixture is missing. A script, `scripts/check-interop-abi-history.py`, rebuilds the writer and reader of every release tag in CI, checks that each tag still emits its persisted bytes, and runs every released reader over every persisted blob.

A released minor's fixture is append-only: the script fails when a tag's fixture is missing or differs, and its `--write` mode refuses to overwrite a file whose bytes changed. A fixture for a minor that only `master` writes is provisional: an ABI bump that supersedes that minor before any release removes its files and manifest rows in the same pull request, as minors 1.1 and 1.2 left none. `--write` performs that removal only for files whose manifest rows list the current tree as sole writer, and refuses before changing any file when a release tag wrote the file. Release sources are rebuilt without `-Werror`, because a newer compiler's warning in an immutable tag cannot be fixed; the current tree keeps `-Werror`.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Hand-written fixture bytes from the specification (runner-up) | No build of old tags; small | Nothing proves the bytes match what a released writer produced; a misread of the layout is frozen into the fixture | The gap is exactly that no released writer's output is checked |
| Blobs captured from FFmpeg filter runs at each tag | Covers the real producers | Needs an FFmpeg build per tag and a Vulkan device; float values vary by driver, so the bytes are not reproducible | Not deterministic; the producer path is covered by the lavapipe lane |
| Extend `interop_test.c` | One conformance file | VMAFx mirrors the file byte for byte, so every change forces a VMAFx change | Couples an internal history check to the shared fixture |
| Per reader and blob expected-output dumps | Reader results persisted as text | Reader count times blob count files, all regenerated on every bump | Assertions against the shared values cover the same cases without the matrix of files |

## Consequences

- **Positive**: A wire change to an existing section, a reader that rejects an older release's blob, or a released reader that fails on a newer blob now fails the fast suite or the CI core job. Every ABI bump must add the fixture for the new minor, because the current-writer test fails without it.
- **Negative**: The CI core job checks out the full history and tags (`fetch-depth: 0`). The program carries `#if PELORUS_ABI_MINOR` gates for each minor; a new section adds a fill and a check function.
- **Neutral / follow-ups**: The released-tag check needs a POSIX C compiler and is skipped with a stated reason on Windows; the persisted-blob tests in the fast suite run on every platform. The FFmpeg filter producers and the VMAFx reader are outside this check.

## References

- Issue #467 (gap audit of 2026-10-12, section C, row C14-02; plan node TL1-VMAFx-pelorus-C14-2).
- [ADR-0103](0103-interop-sidedata-abi.md) (side-data ABI rules R1 to R6), [ADR-0183](0183-sidedata-zero-free-carrier.md) (carrier form, ABI 1.5), [ADR-0214](0214-quality-window-section.md) (ABI 1.6).
- Maintenance ledger order `fleet-tl1-fix-wave-20261027` (paraphrased: each gap-audit section C row becomes a plan node closed by a CI test and a planted red run).
