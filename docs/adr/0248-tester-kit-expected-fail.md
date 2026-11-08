<!-- markdownlint-disable MD013 -->
# ADR-0248: Kit-shipped fast tests carry their expectation in the binary

- **Status**: Accepted
- **Date**: 2026-11-08
- **Deciders**: Lusoris
- **Tags**: test, tester, meson, build, ci

## Context

The Pelorus tester kit builds and ships a relocatable native test suite under `/opt/pelorus/tests/` (ADR-0173, ADR-0180). This suite is packaged from the native build via `meson introspect --tests` and installed by `tools/tester/install-fast-suite.py` into a shell runner, `/opt/pelorus/tests/run-fast-suite.sh`. The kit runner executes tests sequentially under `/bin/sh` with `set -eu`, expecting every test command to exit with status code 0.

However, `meson introspect --tests` (implemented in `mesonbuild/mintro.py` `get_test_list`) exports only command arguments, environment variables, working directories, timeouts, protocols, and dependencies; it does not export `should_fail`. When a fast test is defined with `should_fail: true` (such as the planted negative tests in PR 578: `interop-history-planted-value` and `interop-history-planted-dir-size`), Meson's test runner expects a non-zero exit code and marks the test as `EXPECTEDFAIL` (passing the overall suite). But when packaged into the tester kit, the runner executes the binary directly without knowledge of `should_fail`, and the intentional non-zero exit code halts the runner, causing tester container image workflows to fail.

Previously, `scripts/check-tester-kit-install.py` only checked that test binaries and fixtures could be copied to a relocatable target directory without path escaping (`..`) or referencing files outside the source and build trees. It never executed the packaged suite. Furthermore, the local build tree typically builds with `-Dtools=true`, whereas the kit builds with `-Dtools=false -Dshaders=false`, so local builds could not detect kit packaging or execution failures before pushing.

## Decision

We decide that:

1. A fast test that the tester kit ships must never rely on Meson's `should_fail: true` option. Instead, any negative test case must carry its assertion and expectation within the test binary itself, exiting with status code 0 only when the expected rejection or validation failure occurs, and exiting non-zero when the system fails unexpectedly or accepts invalid input.
2. The native build and fast-suite installation logic of the tester kit is consolidated into a single script, `tools/tester/build-fast-suite.py`. This script is called by `tools/tester/Containerfile` during container image builds and by `scripts/check-tester-kit-install.py --run-kit` during local verification in `make test`. As a result, local test gates execute the exact suite that the kit packages and runs in CI.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Kit installer reads `should_fail` from `meson-private/meson_test_setup.dat` | Preserves Meson `should_fail` declarations without modifying tests | Reads an undocumented Python pickle file private to Meson; fragile across Meson versions; unsafe deserialization | Meson internal state formats are explicitly private and subject to breaking changes without notice |
| Kit image runs `meson test` and reads `testlog.json` EXPECTEDFAIL | Standard Meson runner behaviour; supports `should_fail: true` | `should_fail` also passes when a test fails for an unrelated reason, such as a missing fixture; the assembled images would need the build tree plus Meson and Ninja, which `tools/tester/Containerfile` installs only in the `build` stage | Adds a toolchain and a build tree to every assembled image and keeps the weaker expectation |
| Static ban of `should_fail` in `libpelorus/` Meson build files | Fast static check in linter or pre-commit | Misses other runtime kit-only failure classes such as binary relocation, missing shared libraries, environment scrubbing, and exit 77 skips | Only covers the syntax of Meson files, leaving other relocation and runtime gaps unprotected |
| Self-contained binary expectations with single-source build runner (chosen) | Tests are self-verifying; exit code 0 unambiguously denotes test success; unified build script ensures identical execution in local gates and container images | Negative tests must invert their exit logic in code; `--run-kit` compiles and runs the fast suite in a temporary directory during local testing | The local gate runs the suite the kit ships, the way the kit runs it, so relocation, environment and exit-code gaps show up before push, not only in the non-required `tester-publish` workflow |

## Consequences

- **Positive**: Discrepancies between Meson test execution and the kit runner are detected locally in `make test` before pushing to CI. Negative test cases explicitly verify rejection and distinguish intentional negative outcomes from environmental or execution errors. The single-source script `tools/tester/build-fast-suite.py` keeps container image builds and local verification in lockstep.
- **Negative**: Tests written for the native kit cannot use Meson `should_fail: true`. `make test` (and so `make verify-all`) builds and runs the kit fast suite a second time in a temporary directory; measured on the workstation, the build and full run take 5 s with a warm compiler cache, and the CI kit build step takes 3.5 s on arm64. A failed kit build prints the step name and the last 20 lines of its output.
- **Neutral / follow-ups**: Fast tests for developer tools under `tools/` continue to use `should_fail: true` because they are excluded from the kit (`-Dtools=false`). If developer tools are ever packaged into the kit in the future, `--run-kit` will catch and enforce this invariant on them. PR 578 moves its two planted tests to the reader's `expect-reject` subcommand in the same change, so the kit suite runs green.

## References

- Controller order `tl1-vmafx-pelorus-c14-2`, 2026-11-08, tester-kit expectation (paraphrased): fix the planted tests and add a defence whose planted case fails without the fix.
- Issue #467 (gap audit C14-02).
- PR 578 (interop ABI history fast suite).
- Meson test introspection: `mesonbuild/mintro.py` `get_test_list`.
- `tools/tester/Containerfile`.
