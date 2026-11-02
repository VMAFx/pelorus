<!-- markdownlint-disable MD013 -->
# ADR-0239: Adopt the VMAFx float-identity Semgrep rule in test suites

- **Status**: Accepted
- **Date**: 2026-11-02
- **Deciders**: Lusoris
- **Tags**: ci, testing, standards, semgrep

## Context

CodeQL's query `cpp/equality-on-floats` reports direct equality (`==` and `!=`) between two computed floating-point values. However, CodeQL only reads compiled translation units during the full build and does not run locally as a fast pre-push gate.

In the sibling VMAFx/vmafx repository, ADR-1502 introduced the Semgrep rule `vmaf-test-float-identity-via-float-bits` (commit 25ee5f3a25aebc97ee453fc3a70f676c68da2a81). It inspects test sources and catches comparisons between computed floating-point values while exempting literals, macros, k-constants, NaN idioms (`x != x`), integer casts, and integer-returning calls.

Pelorus already clears the one known comparison in `libpelorus/test/interop_test.c:1954` via `float_bits_equal()` (PR #350); the planted revert of that line is the case the rule must refuse. Adopting this rule directly in Pelorus protects against float-comparison regressions in test sources across `libpelorus/test/**` and `ffmpeg-patches/test/**`.

## Decision

1. Add `.semgrep.yml` at repository root defining `pelorus-test-float-identity`. Its `patterns` block starts from VMAFx/vmafx `vmaf-test-float-identity-via-float-bits`; the id, the message (pointing at `float_bits_equal()`) and the paths are Pelorus's own, scoped to `/libpelorus/test/**` and `/ffmpeg-patches/test/**`. Review of PR #481 found four gaps in the upstream block, and Pelorus closes them:
   - the NaN idiom (`x != x`) exempts call-free operands only, so `next() == next()` stays in scope;
   - the literal exemption also covers hexadecimal and suffixed integer literals (`0x10`, `1U`, `10ul`);
   - the integer-cast exemption covers every standard integer type (`long long`, `short`, `unsigned char`, `ptrdiff_t` and the rest), not only the fixed-width types;
   - the macro and k-constant exemption trusts the name, so the test refuses any non-const `float` or `double` in the test trees declared with such a name. Every declarator of a declaration is read (`double ordinary, kComputed = f();` names `kComputed`), but only its declared name, so a macro in a prototype (`double f(UINT32 n);`) is not taken for one.
2. Add `.semgrepignore` to prevent Semgrep's default ignore rules from skipping `test/` directories.
3. Add `scripts/test-semgrep-float-identity.py` as a fast-suite test (`meson.build`) with planted defect cases:
   - planted computed comparisons (hits) vs. literals/macros/constants/casts/calls (no hits);
   - planted revert of `libpelorus/test/interop_test.c` (exactly 1 hit);
   - one-side-typed plants (a typed operand against a struct field of unknown type), so each typed pattern pair has a case only it reports. In Semgrep 1.182.1 the `(double $A)` and `(float $A)` forms of one orientation match the same operands, so a mutation removes the pair; every pair mutant, the arithmetic branch mutant and the call-exemption mutant fail the test;
   - planted cases for each of the four narrowings above, each refused by its own mutant;
   - clean repository scan assertion (0 hits), which also requires that the scan read a test source from both `libpelorus/test/` and `ffmpeg-patches/test/`, so an ignore rule that hides either tree fails the test instead of passing it empty.
   The hit lines come from the `/* reported` comments in the planted source, and every Semgrep run stops after 300 s.
4. Add a `semgrep` CI job in `.github/workflows/ci.yml` running in the pinned container `mirror.gcr.io/semgrep/semgrep:1.182.1@sha256:451b3d875c00dac7ed5db5293e584a07de11449a409c10b3c2d2652f429630b6`, the release the fast-suite test was run with locally; the job fails when the test skips. Lanes without `semgrep` on `PATH` skip the fast-suite test with the reason listed in `.config/test-skips.json`.

## Alternatives considered

| Option | Result | Why not chosen |
| --- | --- | --- |
| Rely on CodeQL `cpp/equality-on-floats` only | Operates only on compiled translation units during full builds | Requires the full compiled tree and does not run in the fast suite or before a push; a test source outside the build is not read |
| Download Semgrep registry rulesets dynamically | Non-deterministic, unpinned remote downloads at run time | Violates pinning and determinism requirements (ADR-0190) |
| Local pinned Semgrep rule file and test (chosen) | Fast, deterministic, runs in fast test suite and CI | — |

## Consequences

- Direct `==` or `!=` comparisons on computed floats in `libpelorus/test/` or `ffmpeg-patches/test/` fail fast locally and in CI.
- Tests must compare floating-point values via bit patterns (`float_bits_equal()`) or explicit tolerance.
- The rule is verified by planted-defect regression tests executed on every fast-suite run.
- Every pull request reports the `semgrep` job, so the engine renders it as a required check: `.github/rulesets/main.json` lists it in job-id order, and the maintainer applies the ruleset with `praetorctl sync --remote` after this change lands.
- The new job inherits the shared "Load build configuration" step. Review found its `while read` loop had no bound, so all 13 copies across four workflows now select the three keys with one `grep -E` that fails when none matches, and `scripts/check-build-config.py` refuses a loop in that step (planted case "looping build-config load"). The keys, the `grep` and the append to `"$GITHUB_ENV"` must sit in that step itself; the same words in another step of the job do not count (planted cases "build-config tokens elsewhere", "echo build-config load" and "build-config load missing append").

## References

- VMAFx/vmafx rule `vmaf-test-float-identity-via-float-bits` (`.semgrep.yml` at 25ee5f3a25aebc97ee453fc3a70f676c68da2a81).
- VMAFx/vmafx test `scripts/ci/tests/test_semgrep_float_identity.py`.
- Pelorus PR #350 (`float_bits_equal` in `libpelorus/test/interop_test.c`, commit 796693b).
- Fleet order `pelorus-interop-float-identity-20261101`, 2026-11-01 (paraphrased): adopt the VMAFx float-identity rule, clear any hit with a form the rule accepts without changing what a test compares, and prove the rule with a planted revert of the PR #350 comparison that fails without the fix.
- [ADR-0190](0190-pinned-toolchain-provenance.md): pinned toolchain provenance over unpinned registry downloads.
