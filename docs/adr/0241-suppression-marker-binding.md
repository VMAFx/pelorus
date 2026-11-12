<!-- markdownlint-disable MD013 -->
# ADR-0241: Bind every nosemgrep and nosec marker to its start line, rule id and reason

- **Status**: Accepted; amended by [ADR-0256](0256-mc-sad-gain-wall-time-threshold.md) (formatter suppression comments refused)
- **Date**: 2026-11-02
- **Deciders**: Lusoris
- **Tags**: testing, security, static-analysis, ci

## Context

Codacy runs Bandit and Semgrep on pull requests, and a finding the code answers on purpose is silenced with an inline marker: `# nosec <Bxxx>` for Bandit, `# nosemgrep: <rule id>` for Semgrep. Nothing checked those markers, and three ways of writing them hide more than intended or nothing at all:

- **Misplaced line.** Semgrep 1.182.1 honours a marker on the start line of a finding or on the line before it. A marker on an argument continuation or on the closing bracket of a multi-line call suppresses nothing, so the finding stays and the marker only looks like an answer (measured 2026-11-02 with a probe rule over one call per placement: the argument-line, closing-bracket and wrong-id cases stay flagged, the start-line, line-before and bare cases do not).
- **Bare marker.** `# nosemgrep` without rule ids, and `# nosec` without test ids, silence every rule on that line, including rules added later.
- **Missing reason.** A marker without a reason gives a reviewer nothing to judge. On master, `ffmpeg-patches/test/doc-recipes-smoke.py:79` named its Semgrep rule but no reason.

VMAFx/ffmpeg-patches `tests/test_subprocess_nosec.py` (2026-11-01) checks the same idea for subprocess calls: a `nosec` comment with a rationale on one of the call's lines, read with `tokenize` so that marker text inside a string literal does not count.

## Decision

`scripts/check-suppression-markers.py` reads every tracked Python file (`git ls-files`, `.py`, `.pyi` or a Python shebang) in its declared encoding (`tokenize.open`; a file that does not decode is a finding) and refuses a marker comment unless:

1. it sits on the start line of a statement or expression in the file's AST, which rules out comment-only lines, argument continuations that start no node, and closing-bracket lines;
2. a Semgrep marker reads `nosemgrep: <id>[, <id>...] - <reason>`, each id being a rule of the local `.semgrep.yml` or a full registry id with at least two dots (`python.lang.security.audit.<rule>.<rule>`);
3. a Bandit marker reads `nosec <Bxxx>[, <Bxxx>...] - <reason>`.

The `nosem` alias and a marker word in the middle of a comment are refused as well, since Semgrep may read either as a suppression. A marker in a tracked file that is neither Python nor Markdown fails closed, because the check binds Python only; such a file is read with undecodable bytes kept, so a non-UTF-8 file is scanned, not skipped, and every file is also read as both little-endian and big-endian UTF-16 from byte offsets 0 and 1, since the NUL bytes of a UTF-16 file would otherwise split the marker word. No detection heuristic decides which reading applies: all five readings (UTF-8; LE and BE at offsets 0 and 1) are scanned and their findings merged, so a byte-order mark, the share of NUL bytes or one stray leading byte cannot hide a marker; both byte orders are needed because a trimmed odd tail drops the last character of the other order, so a marker ending at the last byte of the file needs its own order. A file the check cannot read is a finding, not an abort. A marker after prose in the same comment counts when it follows its own `#`, since Semgrep honours it ([research digest](../research/0482-suppression-marker-placement.md)). Both marker kinds may share one comment line (`# nosec B603 - ...  # nosemgrep: <id> - ...`); Semgrep 1.182.1 honours that form and a comma-separated id list with a reason (measured 2026-11-02).

The rule is stricter than Semgrep on one point: a marker on the line before the start line suppresses in Semgrep but fails here, so each marker has one place, beside the code it answers.

`--self-test` plants a marker on the line before, on a closing bracket, a bare `nosemgrep`, a `nosemgrep` without a reason, a short id, the `nosem` alias, a bare `nosec`, a `nosec` without a reason, a marker word mid-comment, a marker in a shell file, a marker in a non-UTF-8 shell file, a bare marker in a `.pyi` stub, an undecodable source, a marker in a UTF-16 file with a byte-order mark, without one, with an odd trailing byte, with few NUL bytes (CJK text), after one stray byte and in big-endian order, a file that looks like UTF-16 but carries a plain ASCII marker, and an unreadable file (skipped when running as root, or on Windows, where mode 0 leaves a file readable); each must be refused, and a valid source with marker text in a string literal and a declared latin-1 source must pass. The meson fast-suite test `suppression-markers-self-test` runs the planted cases on every platform (`--self-test-only`); `suppression-markers` runs them and then the tree scan, on every platform except Windows, whose MSYS2 leg has no git on `PATH`.

Formatter suppression comments are also refused in tracked Python: `fmt: skip/off/on`, `yapf: disable/enable`, and `autopep8: off/on`, case-insensitively with spaces around the colon allowed ([ADR-0256](0256-mc-sad-gain-wall-time-threshold.md)). The self-test plants `x = 1  # fmt: skip`, a `# fmt: off` / `# fmt: on` pair, a lone `# fmt: on`, `# yapf: disable`, `#fmt:skip`, and `# FMT: SKIP`; each must be refused. Additional plants cover a spaced colon, `yapf: enable`, and both `autopep8` markers. A string literal containing `# fmt: skip` must pass.

## Alternatives considered

| Option | Result | Why not chosen |
| --- | --- | --- |
| Run Semgrep with `--disable-nosem` and match each marker to a live finding | Proves each marker answers a real finding | Needs the Codacy registry rules over the network in the fast suite, and Bandit is not installed in the build lanes |
| Port the ffmpeg-patches subprocess test as it is | Covers `nosec` on subprocess calls | Leaves `nosemgrep`, bare markers and markers on other calls unchecked |
| Static AST and comment check over every tracked Python file (chosen) | Hermetic, runs in every lane with Python and Git | Binds to any statement or expression start line, not to the exact node a rule reports |

## Consequences

- `ffmpeg-patches/test/doc-recipes-smoke.py:79` gains the missing reason.
- A new marker in a C, shell or YAML file fails until the check learns that language.
- Mutation evidence: eight mutants of the check (no start-line binding, optional `nosemgrep` reason, bare `nosemgrep` accepted, short id accepted, optional `nosec` reason, mid-comment word accepted, string literals counted, UTF-8 with replacement instead of the declared encoding) each fail the self-test; nine more (`.pyi` read as non-Python, non-UTF-8 non-Python file skipped, odd trailing byte kept, unreadable file unhandled, and each of the five readings dropped: drop UTF-8, drop LE offset 0, drop LE offset 1, drop BE offset 0, drop BE offset 1) fail it after the review follow-up, and a marker pattern that matches every comment fails on the clean UTF-16 and CJK files the self-test also plants.

## References

- [Research digest 0482](../research/0482-suppression-marker-placement.md): where Semgrep 1.182.1 honours a marker.
- VMAFx/ffmpeg-patches `tests/test_subprocess_nosec.py` at aa70570.
- [ADR-0188](0188-named-test-skips.md): named fast-suite skips.
- Fleet order `pelorus-nosemgrep-binding-check-20261102`, 2026-11-02 (paraphrased): port a mechanical check that every nosemgrep and nosec marker binds to the flagged start line and names the full rule id and a reason, modelled on the VMAFx subprocess test, with planted cases for a misplaced line, a bare marker and a missing reason.
