<!-- markdownlint-disable MD013 -->
# ADR-0244: Parse every Python command line with argparse

- **Status**: Proposed
- **Implementation**: pending (#496)
- **Date**: 2026-11-04
- **Deciders**: Lusoris
- **Tags**: testing, static-analysis, scripts

## Context

The gate and helper scripts under `scripts/` and `ffmpeg-patches/test/` read their command lines by hand: `argv[argv.index(flag) + 1]`, `argv[1]`, `"--x" in argv`, `argv == ["--self-test"]`. Hand parsing has no bounds check and no usage error. `python3 -I scripts/check-mirror-contract.py --list` raised `IndexError` instead of reporting the missing value ([#496](https://github.com/VMAFx/pelorus/issues/496)), and 25 tracked scripts used the same pattern. `scripts/windows/check-pe-imports.py` also read its INI allow-list by hand and kept the last of two duplicate entries. No Ruff 0.17.3 rule covers hand-parsed argv (checked with `ruff rule --all`, 2026-11-04), so the pinned linter of [ADR-0243](0243-ruff-zizmor-local-gate.md) cannot catch the class.

## Decision

We will parse every Python command line in the tree with stdlib `argparse` and read INI files with stdlib `configparser`, and enforce the first with `scripts/check-python-argv.py` in the fast suite.

- **Rule 1.** A `sys.argv` reference is allowed only as `sys.argv[0]` or as `sys.argv[1:]` passed directly to a call (`main(sys.argv[1:])`).
- **Rule 2.** A subscript, `.index()` call, or `in`, `not in`, `==` or `!=` comparison on a name `argv` is refused in a module that has a command line (it reads `sys.argv` or defines `main` or `parse_args`) and does not import `argparse`, and inside a function named `main` or `parse_args` in every module. `argv is None` stays allowed. A module with no command line, such as a test helper whose `argv` is a recorded subprocess call, is out of scope.
- **Exceptions.** Two files are listed with a reason in the checker: `.config/agent/hooks/block_evasion.py` (a Praetor-managed hook this repository does not edit) and `ffmpeg-patches/test/doc-recipes-stub-ffmpeg.py` (a stand-in that mimics FFmpeg's own option syntax, which `argparse` cannot express).
- **INI.** `check-pe-imports.py` reads the DLL allow-list with `configparser` (its default strict mode) and refuses unknown or duplicate sections and duplicate bundle DLLs; its self-test plants each refusal.
- **Defaults.** Parsers keep `argparse`'s default `allow_abbrev=True`; a unique prefix of a long option is accepted as it is in every other stdlib-based tool.
- **Wiring.** Meson fast tests `python-argv` (tree scan) and `python-argv-self-test` (15 planted cases) run in the fast suite; the tree scan is skipped on Windows, where the runner has no `git` on `PATH`, and the Linux jobs run it on every pull request.

Scope is part 1 of #496. The shared C argv helper for `tools/pelorus_*.c`, the CSV helper merge and the shared JSON shape validator follow in their own pull requests.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Keep hand parsing, add bounds checks per script (runner-up) | Smallest diff; no behaviour change for existing calls | Each script re-implements usage errors; nothing stops the next hand parser; the bug class stays open | Fixes the reported crash only; the class recurs |
| Ruff or Pylint rule instead of an own AST checker | No extra script to maintain | No Ruff 0.17.3 rule covers hand-parsed argv; a custom Pylint plugin is more code than the checker and adds a dependency | Nothing to enable; a plugin costs more than the 392-line stdlib checker |
| Third-party parser (click, typer) | Richer help output, subcommands | New dependency for every script; breaks the stdlib-only `python3 -I` contract of the gate scripts | Rule 9 of the project contract asks for a reason a new dependency wins; none here |

## Consequences

- **Positive**: every script gives a usage error and exit code 2 on a missing or unknown argument instead of a traceback; `--help` works everywhere; a new hand parser fails the fast suite before CI.
- **Negative**: the checker is one more script to maintain; Rule 2 keys on the name `argv`, so a hand parser on a renamed list is not caught (Rule 1 still catches its `sys.argv` source in a command-line module).
- **Neutral / follow-ups**: #496 parts 2 to 4 (C argv helper, CSV helper, JSON shape validator).

## References

- Issue [#496](https://github.com/VMAFx/pelorus/issues/496), repository audit of 2026-11-04.
- Controller order `pelorus-ruff-zizmor-gate-verified` (paraphrased): after the lint-gate follow-up, take the next frontier unit, which is #496 part 1.
- [ADR-0243](0243-ruff-zizmor-local-gate.md): pinned Ruff in the local gate.
