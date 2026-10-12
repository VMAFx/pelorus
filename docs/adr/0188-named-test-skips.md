<!-- markdownlint-disable MD013 MD060 -->
# ADR-0188: A skipped test passes only when it is named with its reason

- **Status**: Accepted
- **Date**: 2026-10-12
- **Deciders**: Lusoris
- **Tags**: testing, ci, build

## Context

`meson test` exits 0 when a test exits 77 (SKIP), whatever the reason. Two fast-suite
tests skip by design on every CI lane and in `make verify-native`:

- `telemetry-field-parity` runs the registry and `interop.h` rules, then skips the
  VMAFx comparison until VMAFx publishes its field list (issue #220, ADR-0174).
- `vulkan-drm-map` is a GPU smoke (ADR-0184); it skips when `FFMPEG_BIN` is unset.

Nothing distinguished these planned skips from an unplanned one. With `FFMPEG_BIN`
pointing at an FFmpeg without the Pelorus filters, `vulkan-drm-map` skipped with a
different reason and `meson test` still exited 0: the check reported green while
the thing it guards was broken.

## Decision

A SKIP counts as a pass only when `.config/test-skips.json` lists the test with the
exact reason line it printed (`SKIP: ...`) and the issue or ADR that keeps the skip.

- `scripts/check-test-skips.py` reads the run's `meson-logs/testlog.json` and fails
  on every other skip, including a listed test that skips for another reason. A
  missing, empty or malformed log or list exits 2, never 0.
- `make test` and every CI fast-suite run (`core`, `sanitizers`, `windows`,
  `release-build`) call it right after `meson test`.
- `test-skips-self-test` in the fast suite plants an unlisted skip, a listed test
  with another reason, a skip without a reason line and malformed inputs, and
  requires each to be rejected.
- An entry leaves the list when its test stops skipping in CI.

## Alternatives considered

- **Fail on every skip.** Rejected: both tests skip by design until an external
  input exists (a VMAFx field list, a GPU host).
- **Allow by test name only.** Rejected: the planted case above is a listed test
  skipping for an unplanned reason; only the reason line separates them.
- **Move the GPU smoke out of the fast suite.** Leaves the parity skip unguarded,
  and the smoke's self-test and default skip are what keep it from rotting.

## Consequences

- A new skip, or a changed skip message, fails the gate until the list names it.
- The NVENC smoke inside `ffmpeg-patches/test/build-and-run.sh` accepts exit 77 on
  its own; it is outside Meson and follows in a separate change.

## References

- Gap audit 2026-10-12, row B (gates passing on skip); fleet order
  `fleet-green-while-broken-20261012`.
- Planted failure: `FFMPEG_BIN=/bin/true make test` fails with
  `pelorus:vulkan-drm-map skipped and is not listed with this reason`; without the
  check `meson test` exits 0.
