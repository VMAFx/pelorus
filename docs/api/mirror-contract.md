<!-- markdownlint-disable MD013 -->
# VMAFx mirror contract

VMAFx vendors ten Pelorus files as a read-only copy pinned to one released
Pelorus commit. This page says which files, how the pin moves, and what
each side does when something changes. Decision records:
[ADR-0103](../adr/0103-interop-sidedata-abi.md) (single-sourced ABI),
[ADR-0174](../adr/0174-encoder-telemetry-abi-1-4.md) (ABI 1.4 and the ten-file
scope), VMAFx ADR-1113 (the mirror) and VMAFx ADR-1276 (released-commit pins).

## If you touch a mirrored file

1. The change must be on `master` before VMAFx re-pins to it. VMAFx pins
   released commits only (tag `vX.Y.Z`), so ship it in a release.
2. Tick the "VMAFx mirror" item in the pull-request checklist and name the
   follow-up VMAFx issue in the PR body.
3. If the change adds or moves a wire field, or adds a wire form, bump
   `PELORUS_ABI_MINOR` and add the ABI entry (see "ABI changelog").
4. Keep the file renderable by the VMAFx sync script: a source opens with a
   `/** ... */` licence comment holding exactly one SPDX licence line, naming
   `EUPL-1.2`, then a blank line, and has at most
   one `#include "pelorus/..."`. The fixture holds that one SPDX line before its
   first `#include "pelorus/..."`. `make docs-check` runs
   `scripts/check-mirror-contract.py`, which enforces both shapes.

## Mirrored files

The authoritative list is [`libpelorus/mirror-paths.txt`](../../libpelorus/mirror-paths.txt).
It mirrors the VMAFx manifest (`scripts/ci/pelorus-mirror-paths.txt`). The
VMAFx destination of each file follows in the second column.

| Pelorus path | VMAFx path |
| --- | --- |
| `libpelorus/include/pelorus/pelorus.h` | `core/include/libvmaf/pelorus/pelorus.h` |
| `libpelorus/include/pelorus/interop.h` | `core/include/libvmaf/pelorus/interop.h` |
| `libpelorus/include/pelorus/deband.h` | `core/include/libvmaf/pelorus/deband.h` |
| `libpelorus/include/pelorus/denoise.h` | `core/include/libvmaf/pelorus/denoise.h` |
| `libpelorus/src/interop.c` | `core/src/interop/pelorus_interop.c` |
| `libpelorus/src/deband_params.c` | `core/src/interop/pelorus_deband_params.c` |
| `libpelorus/src/denoise_params.c` | `core/src/interop/pelorus_denoise_params.c` |
| `libpelorus/src/qp_report_csv.c` | `core/src/interop/pelorus_qp_report_csv.c` |
| `libpelorus/src/version.c` | `core/src/interop/pelorus_version.c` |
| `libpelorus/test/interop_test.c` | `core/test/test_pelorus_interop.c` |

Not mirrored at ABI 1.4: `telemetry.c`, `encode_record.c`, `sha256.c`,
`telemetry.h`, `encode_record.h` and `schema/telemetry-fields.json`
(ADR-0174 decision 11). Adding any of them is a change to the VMAFx manifest
first: VMAFx rejects a tracked file outside its exact path list.

VMAFx edits each copy in two ways only: a `VENDORED FROM ... DO NOT EDIT`
banner after the licence header, and the rewrite of the one intra-Pelorus
include from `pelorus/<x>.h` to `libvmaf/pelorus/<x>.h`. The fixture also gets a
VMAFx-authored prefix. Everything else is byte-identical and checked through
end of file.

## Pin rule

- VMAFx sets `PELORUS_VENDOR_SHA` in `scripts/sync-pelorus-interop.sh` to a
  released Pelorus commit: the commit a `v*` tag points at (VMAFx ADR-1276).
  Unreleased `master` commits are never pinned.
- The pinned commit must stay reachable in `VMAFx/pelorus`. Tags are never
  moved or deleted, and `master` history is never rewritten past a released
  commit.
- VMAFx reads the pinned git object, not a working tree. Its CI checks out
  `VMAFx/pelorus` at the exact SHA; a checkout without that object fails
  closed ("pinned Pelorus commit is unavailable").
- The "released commits only" rule is a review rule (VMAFx ADR-1276). No
  script checks that the pinned SHA is tagged or on `master`.

## Re-pin triggers

VMAFx re-pins, re-vendors with `scripts/sync-pelorus-interop.sh --update`,
reruns the drift check and runs the sanitizer job, when a Pelorus release:

| Trigger | Example |
| --- | --- |
| bumps `PELORUS_ABI_MINOR` | ABI 1.4 (ADR-0174); ABI 1.5, the zero-free carrier that NVENC streams carry: VMAFx also calls `pel_blob_unwrap()` before parsing (ADR-0183) |
| fixes parser correctness or safety, even with the ABI number unchanged | v0.2.2 aligned-access fix (VMAFx ADR-1276) |
| changes any other mirrored file in a way that alters its bytes | `pel_result_str` text in `version.c`; fixture cases |

A change that touches none of the ten files needs no re-pin.

## ABI changelog

- Per release: `CHANGELOG.md` (rendered from `changelog.d/`), one entry per ABI
  minor, tagged "interop ABI".
- Per layout: [`docs/api/interop-abi.md`](interop-abi.md) sections "ABI 1.4" and
  "Zero-free carrier form (ABI 1.5)" and the matching ADRs hold the
  before/after C snippets for readers (the `Migration:` commit footer when a
  change breaks one).
- The ABI is append-only and the `PELORUS_ABI_MAJOR` stays 1; see the stability
  rules in [`interop-abi.md`](interop-abi.md).

## What VMAFx changes per re-pin

| VMAFx file | Change |
| --- | --- |
| `scripts/sync-pelorus-interop.sh` | new `PELORUS_VENDOR_SHA`; the script re-renders banners from the new SHA and refuses a source whose licence comment does not name `PELORUS_MIRROR_LICENSE` |
| `scripts/ci/pelorus-mirror-paths.txt` | only when the file set changes; it must match the script manifest |
| the ten vendored files | regenerated by `--update`, never edited by hand |
| `docs/api/pelorus-interop.md` | pin and ABI version text |

On a licence change in Pelorus (EUPL-1.2 since ADR-0171, from BSD-2-Clause-Patent
in the earlier mirror), VMAFx also updates, in the same re-pin:

| VMAFx file | Duty |
| --- | --- |
| `REUSE.toml` | the `core/src/interop/pelorus*` annotation names the licence of the vendored files |
| `.config/lint-exceptions.d/spdx.toml` | the ten per-file exceptions for the missing SPDX line (expiring 2026-12-31) are removed: every mirrored file now carries its own SPDX line |
| `docs/credits.yaml` | the `pelorus` entry (`license`, `paths`) |
| `scripts/sync-pelorus-interop.sh` | `PELORUS_MIRROR_LICENSE` names the new licence; the old renderer cut a fixed 17-line header and could not render the 6-line EUPL-1.2 one (VMAFx/vmafx#2649 replaced it) |

Pelorus does not edit VMAFx files; it lists these duties in the issue that
announces the release.

## When the pinned SHA is not on master

A change to a mirrored file must be in a release before VMAFx pins it. No
script enforces this, so review does:

| Case | Behaviour |
| --- | --- |
| SHA is a tagged release on `master` | normal; nothing to do |
| SHA is on `master` but not tagged | VMAFx review rejects the pin; Pelorus cuts the release first |
| SHA is on no `master` history (topic branch, squashed pull-request head) | VMAFx review rejects the pin. GitHub can still serve such a SHA through `refs/pull/N/head`, so the drift check may pass; VMAFx re-pins to the release that contains the change |
| SHA is not fetchable at all (deleted branch, rewritten history) | the drift check fails closed ("pinned Pelorus commit is unavailable"); VMAFx re-pins to the next release |

Never pin a pull-request or topic-branch commit: a squash merge replaces it,
and its bytes are not what `master` ships.
