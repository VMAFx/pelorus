<!-- markdownlint-disable MD013 MD060 -->
# ADR-0257: Transcript cases may feed one fixture file to the tool's stdin

- **Status**: Accepted
- **Date**: 2026-11-14
- **Deciders**: Lusoris
- **Tags**: test, tools

## Context

[ADR-0249](0249-tool-cli-transcript-tests.md) runs each command with stdin from the null device. `tools/pelorus_zones.c` and `tools/pelorus_qloop.c` read shots and history only from stdin. Their CSV number grammar (issue #583) therefore needs stdin fixtures to be pinned by `tools/transcripts/pelorus_zones.t` and `tools/transcripts/pelorus_qloop.t`.

## Decision

Allow `% stdin <fixture>` once per case, naming a file under the transcript fixtures directory. Apply the existing 8 MiB fixture cap (`tools/transcript.py`, `_execute_directive`). Without the directive, stdin remains the null device. A write error or a pipe still open 5 s after the tool exits fails the case with verdict `stdin-error` (`capture`, `_finish_writer`). This amends ADR-0249; its no-shell rule stands.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Add file options such as `--shots` or `--history` (runner-up) | Existing fixture arguments could supply input | Changes the user-facing CLI and docs; leaves the stdin path untested | Keep the stdin interfaces in `tools/pelorus_zones.c` and `tools/pelorus_qloop.c` covered directly |
| Shell redirect `< file` in the transcript | Familiar syntax | Requires a shell between transcript and executable | Rejected by ADR-0249's no-shell rule |
| Inline stdin bytes in the `.t` file | Input stays beside expectations | A line format cannot keep CR, NUL or a missing final newline byte-exact | Fixture files preserve the bytes read by `tools/transcript.py`, `_execute_directive` |

## Consequences

- **Positive**: The zones and qloop transcripts can pin stdin number grammar; `tools/transcript.py`, `_run_stdin_capture_plants`, checks pipe capacity, early exit, blocked timeout and stdin held by a child.
- **Negative**: The driver must bound an input writer as well as output readers; the child-held-input plant in `_run_stdin_capture_plants` exercises the shared 5 s drain deadline.
- **Neutral**: `tools/transcript_parse.py`, `_parse_directive` and `_append_directive`, refuses malformed and duplicate stdin directives; `tools/transcript_plants.py`, `stdin_plants`, carries the parser refusal cases.

## References

- [ADR-0249](0249-tool-cli-transcript-tests.md).
- Issue #583; PR #619.
- Source: `req` (paraphrased, 2026-11-14, controller order pelorus-bug-583-20261114): fix issue 583, number grammar of the tool options and CSV fields.
