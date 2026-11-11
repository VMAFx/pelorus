<!-- markdownlint-disable MD013 MD060 -->
# ADR-0255: Copy mapped device memory before element-wise host reads

- **Status**: Accepted
- **Date**: 2026-11-11
- **Deciders**: Lusoris
- **Tags**: vulkan, performance, zero-copy

## Context

The [motion-estimation wall-time measurements](../metrics/mc.md) attribute almost all of the old host cost to two operations: the O(n²) p95 scan and element-wise reads of mapped GPU buffers. PR #545 guards the p95 half with `ffmpeg-patches/test/mc_stats_test.c`; it cannot detect a caller passing mapped memory directly to `attach_motion()` again. The current `mc_snapshot()` in `ffmpeg-patches/files/vf_pelorus_mc_vulkan.c` copies each mapped span once before host processing. Issue #458 needs that second half held too.

The existing [device-local allocation rule](0222-device-local-storage-buffers.md) already scans the relevant C files, headers and added side of hand-maintained patches. The same scan can reject direct mapped reads without a GPU runner.

## Decision

Extend `scripts/check-vulkan-buffer-domain.py`, fast test `vulkan-buffer-domain`, to classify every `mapped_mem` token after line splicing and comment/literal masking in `scripts/c_scan.py`. Patch context participates in lexical masking before only additions are retained; diagnostics keep patch line numbers. Permit complete pointer expressions only in `memcpy` arguments 0 and 1, `memset` argument 0, and the documented write-only argument 3 of `pel_mc_build_predictors` (`pelorus_mc_stats.h:66–84`) and `pelorus_qpmap_raster` (`vulkan-pelorus-qpmap.patch:1313–1345`); indices are zero-based. Member-qualified callees are refused. A mapped destination passed to `memcpy` or `memset` is allowed only when the call is the whole expression statement, optionally prefixed with `(void)`, because those functions return their destination. Permit plain cast stores and non-const local aliases only with allow-listed declaration specifiers and when every later alias use starts a statement and is a plain indexed store. The type allow-list comprises C qualifiers and scalar specifiers, `struct`/`union`/`enum` plus a tag, names ending in `_t`, and names starting with an uppercase letter. Refuse other uses, including compound assignments, and require one bulk `memcpy` before element-wise host reads. Keep three exact-file, exact-statement exceptions for issue #596, entered 2026-11-11 under MEL category D (120 days):

| File under `ffmpeg-patches/files/` | Exact statement after whitespace collapse | Reason |
| --- | --- | --- |
| `vf_pelorus_analyze_vulkan.c` | `base = (const uint32_t *)buf_vk->mapped_mem;` | Per-tile reads scale with resolution |
| `vf_pelorus_denoise_vulkan.c` | `*acc_out = (const PelorusDenoiseBuf *)buf_vk->mapped_mem;` | Fixed 160-word accumulator |
| `vf_pelorus_grain_estimate_vulkan.c` | `if (err >= 0) err = attach_estimate(s, in, (const PelorusGrainBuf *)buf_vk->mapped_mem);` | Fixed statistics struct |

Each exception must cover exactly one refused site. Zero matches, duplicate matches, duplicate `(file, statement)` entries, unknown MEL categories and dates after 2027-03-11 fail the check; 2027-03-11 remains allowed. The pure `check_known()` receives its date explicitly; the CLI supplies today's date. An empty mapped-use scan also fails. `--files DIR` selects a source copy for both allocation and mapped-read checks; its default remains `ffmpeg-patches/files`.

Patch hunk markers (`@@`) reset lexical state. A `+++` line is a file header only immediately after a `---` line, so added `++i;` and `++ i;` remain code. Removed lines and metadata are blank on the new side and do not affect lexical state; `diff`, `index` and `---` lines need no separate reset rules. Comment masking preserves width with spaces. The generic statement-boundary helpers and depth-one comma counter `argument_index()` live beside `call_args()` in `scripts/c_scan.py`; moving the counter preserves its behavior. Self-test cases accept an alias whose first store has an empty prefix and a direct cast store after a closing block, and keep a spaced patch pre-increment without a preceding file header as added code. The [research digest](../research/0458-mapped-read-guard.md#self-test-strength) records all 35 rule mutants exiting 1, including independent removal of each of those three boundaries, and the final file hashes and NLOC counts.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Runtime timing test on a GPU | Needs a GPU runner; GPU wall-time regression coverage remains in #167 |
| clang-tidy or Semgrep rule | No project rule engine for this C flow; the existing regex scan already owns these files |
| Review only | Does not mechanically prevent the direct mapped argument from returning |

## Consequences

- **Positive**: The fast test rejects new direct mapped readers while preserving bulk copy-out, uploads and documented write-only sinks. Its self-tests plant readers, alias escapes, read-modify-write operations and exception failures.
- **Negative**: This is a lexical scan, not a C parser; it performs no preprocessing or macro expansion. Spliced newlines are restored after the next surviving newline, or at EOF, to preserve line counts. Unterminated string/character literals remain unmasked up to their newline or EOF; unterminated block comments remain unmasked through EOF. C23 digit separators such as `1'000` are treated as unterminated character literals, leaving that line unmasked. Bulk destination calls embedded in assignments, conditions or arguments fail closed; so do keyword-led pseudo-declarations (`return`, `else`, `do`) and alias stores within chained assignments or call arguments. The lexical scan fails closed on `mapped_mem + off`, even when a particular offset could be safe. Keyword parentheses (`sizeof`, `if`) around `mapped_mem` and calls through parenthesised names also fail closed. Pointer provenance from `vk->MapMemory` and wrapper implementations is not followed; the rule is not a general C data-flow analysis.
- **Neutral / follow-ups**: Issue #596 replaces the three known reads with copies before their exceptions expire. GPU wall time remains with #167. The patch stack and runtime code remain unchanged.

## References

- Issue [#458](https://github.com/VMAFx/pelorus/issues/458): motion-estimation speedup regression threshold.
- Issue [#596](https://github.com/VMAFx/pelorus/issues/596): three known mapped reads.
- [ADR-0222](0222-device-local-storage-buffers.md): device-local storage buffers.
- [ADR-0116](0116-pelorus-mc.md): motion-vector hints.
- [PR #545](https://github.com/VMAFx/pelorus/pull/545): host p95 regression threshold.
