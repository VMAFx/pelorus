<!-- markdownlint-disable MD013 MD060 -->
# Mapped-memory host-read regression guard

**Date:** 2026-11-11

**Decision:** [ADR-0255](../adr/0255-mapped-memory-host-reads.md).

**Scope:** `scripts/check-vulkan-buffer-domain.py`, fast test `vulkan-buffer-domain`, reader review baseline `56ba1af`, split from local draft `5b44996` (pushed baseline `9ccfe96`), issues #458 and #596. Source evidence comes from C/H files and the masked new side of `ffmpeg-patches/files/*.patch`; line splicing precedes comment/literal masking. Each patch hunk is classified independently with context visible and diagnostics limited to added-line overlap. The default scan reads only `ffmpeg-patches/files/`, excluding the shared FFmpeg fix series applied first by `scripts/fetch-ffmpeg-series.sh`.

## Verdict

The classifier rejects new element-wise host reads through `mapped_mem` while preserving bulk transfers and documented writes. It complements the p95 host-cost test from PR #545; neither check measures GPU wall time, which remains in #167. Three existing reads stay under exact-statement MEL category D exceptions entered 2026-11-11, valid through 2027-03-11. Each entry must match exactly one refused site; stale, duplicated or expired coverage fails.

## Sites

All source paths below are relative to `ffmpeg-patches/files/`. Argument indices are zero-based.

| Site or shape | Classification | Evidence / reason |
| --- | --- | --- |
| `vf_pelorus_mc_vulkan.c:685–688`, `mc_snapshot()` | Allowed | `memcpy` argument 1 copies mapped spans into host scratch |
| `vf_pelorus_mc_vulkan.c:719` | Allowed | `memset` argument 0 writes predictor storage |
| `vf_pelorus_mc_vulkan.c:764`, `pel_mc_build_predictors()` argument 3 | Allowed | `pelorus_mc_stats.h:66–84` writes `prev_mv_pel` only |
| `vulkan-pelorus-qpmap.patch:1203` | Allowed | `memcpy` argument 0 uploads ROI data |
| `vulkan-pelorus-qpmap.patch:1449`, `pelorus_qpmap_raster()` argument 3 | Allowed | Function at patch lines 1313–1345 writes `dst` only |
| `vf_pelorus_denoise_vulkan.c:520–536` | Allowed | Non-const `md` / `cd` aliases used only by plain stores; direct cast stores in the alternate branch |
| `attach_motion(..., mapped_mem, ...)`, cast reads, struct reads, escaped pointers | Refused | No bulk copy before host reads |
| Compound stores, alias reads or calls, `mapped_mem + off`, mapped `memcpy` size | Refused | Read-modify-write or unclassified pointer expression; fail closed |
| `memcpy(dst, sizeof(b->mapped_mem), n);`, `if (b->mapped_mem) x = 1;` | Refused | Keyword parentheses enter the call path and fail closed |
| `memcpy(dst, (reader)(b->mapped_mem), n);` | Refused | The pointer-expression guard rejects calls through parenthesised names before parentheses can merge identifiers |
| `{ const T *p = (const T *)b->mapped_mem; p[0] = 1; }` | Refused | The const-alias rule rejects this shape independently of the store-only-use rule |
| Quoted `"}"` or `'}'` before a later alias read | Refused | Literal masking prevents a quoted brace from ending the enclosing block |
| `log("/*"); reader(b->mapped_mem); log("*/");` | Refused | Literal comment markers cannot hide code |
| A `mapped_mem` token split with a backslash-newline, including spaces/tabs before LF or CRLF | Refused | Splicing joins tokens before lexical masking |
| `s->memcpy(...)`, `ops.memcpy(...)`, `s->pel_mc_build_predictors(...)` | Refused | Member-qualified callees cannot inherit bare-function contracts |
| `attach_motion(..., memset(b->mapped_mem, 0, bytes), ...)`, `T *p = memcpy(b->mapped_mem, src, n);` | Refused | Bulk return values can expose the mapped destination |
| `x = memset(...)`, `if (memcpy(b->mapped_mem, s, n)) ...` | Refused | Destination bulk calls must be complete expression statements |
| `memcpy(b->mapped_mem, s, n) + 0;` | Refused | The bulk destination call's suffix must be exactly a semicolon |
| `{ T *p = (T *)b->mapped_mem; if (x) { p[0] = 1; } consume(p); }` | Refused | Nested braces cannot hide the later escaping alias |
| `return *out = (T *)b->mapped_mem;`, `else *out = ...`, `do *out = ...` | Refused | Keywords are not allow-listed alias declaration specifiers |
| Alias uses `p[0] = p[1] = 0;`, `f(p[0] = 1);` | Refused | Each use must start a statement; chained writes are unclassified even without a C read |
| Alias uses `p[0] = p[1];`, `p[0] = 1 + p[1];`, `q[p[0]] = 1;` | Refused | RHS and index reads are retained as planted cases |
| Unterminated character literal before `reader(b->mapped_mem);` | Refused | Malformed literal contents remain visible to the classifier |
| `x = 'a; reader(b->mapped_mem);` followed by `y = 'b';` on the next line | Refused | The malformed literal stops at the newline; the later valid literal is masked independently |
| Patch addition `/*`, context `*/`, then an added mapped read | Refused | New-side context closes the comment; the refused statement overlaps an added line |
| First hunk adds `/*`; second hunk adds a mapped read followed by context `*/` | Refused | The `@@` reset prevents the second hunk's context from closing a comment in the first hunk and hiding the read |
| `log("mapped_mem");`, `mapped/**/_mem` | Allowed; zero mapped tokens | Literals cannot fabricate tokens; comments separate tokens |
| `(void)memset(b->mapped_mem, 0, n);` | Allowed | The destination return value is explicitly discarded |
| `{ T *p = (T *)b->mapped_mem; p[0] = 1; }` | Allowed; one mapped token | The `alias first store` case accepts the first use with an empty statement prefix |
| `{ if (x) { y = 1; } ((uint32_t *)b->mapped_mem)[0] = 0; }` | Allowed | The `store after block` case starts its statement after the closing brace |
| Patch source `@@ -0,0 +1 @@\n+++ i;` | Added code `\n++ i;` | The `added pre-increment` case retains a spaced increment without a preceding `---` header |
| Plain alias stores after a loop block, an `if` condition, `else` and `do` | Allowed | The `write alias` case exercises statement starts after `}`, `)` and the two keywords |
| `{ T *a = (T *)b->mapped_mem; struct s *c = (struct s *)b->mapped_mem; int *d = (int *)b->mapped_mem; a[0] = 1; c[0] = 1; d[0] = 1; }` | Allowed | Three mapped tokens cover uppercase, tagged and scalar type-name groups; the live denoise scan covers `_t` names |
| `pel_mc_build_predictors(hosts[i = 0, j = 0, k], (const int32_t *)b->mapped_mem, n, prev, &gx, &gy);` | Refused | `subscript sink input` keeps the mapped input at argument 1; bracket commas cannot shift it to the output slot |
| `memcpy(dst[i, 0], b->mapped_mem, n);` | Allowed | `subscript memcpy source` retains the source at argument 1 |
| `ff_vk_create_buf(s, &bufs[i, 0], n, NULL, NULL, STORAGE, HOST_ONLY);` in C or a patch addition | Storage-buffer error | `subscript allocation` and `patch subscript allocation` substitute the Vulkan constants and require the storage diagnostic |
| `pel_mc_build_predictors((S){a, b, c}.p, (const int32_t *)b->mapped_mem, n, prev, &gx, &gy);` | Refused | `brace sink input` retains refusal; the exact argument-span test also verifies brace nesting |
| `memcpy(dst, ((FFVkBuffer *)b->mapped_mem)->mapped_mem, n);` | Refused | `nested mapped source` rejects two mapped tokens before removing casts |
| `memcpy(((FFVkBuffer *)b->mapped_mem)->mapped_mem, src, n);` | Refused | `nested mapped destination` applies the same one-token rule to uploads |
| `pel_mc_build_predictors(h, w, n, ((FFVkBuffer *)b->mapped_mem)->mapped_mem, &gx, &gy);` | Refused | `nested mapped sink` applies the one-token rule to a documented output slot |
| `((uint32_t *)((FFVkBuffer *)b->mapped_mem)->mapped_mem)[0] = 0;` | Refused | `nested mapped store` preserves the existing statement-level token restriction |
| Context `p =` followed by added `memcpy(b->mapped_mem, src, n);` | Refused | `patch result escape` retains the assignment prefix during classification |
| Context `x = reader` ending in a backslash-newline, followed by added `(b->mapped_mem);` | Refused | `patch spliced reader` moves the added origin onto the joined output line; exact scanner cases cover either side of the splice being added |
| Context `T *p = (T *)b->mapped_mem;` followed by added `x = p[0];` | Refused | `patch context alias` extends overlap through the scanned block tail; `patch open alias` covers an absent closing brace |
| Context allocation prefix through the fifth argument, followed by added storage and host-only flags | Storage-buffer error | `patch split allocation` classifies the complete call and checks overlap through its closing parenthesis |
| Context `x = reader(b->mapped_mem);` with unrelated added `y = 1;` before or after it | Unreported | `patch context reader` and `patch reader after addition` exclude context-only statements and leading whitespace from overlap |
| Context-only host-only storage allocation followed by unrelated added `y = 1;` | Unreported | `patch context allocation` requires added-line overlap for the allocation rule too |
| Context `reader(` followed by added `b->mapped_mem);` | Refused | `patch reader argument` retains the enclosing call |
| Added alias declaration followed by a context read through that alias | Refused | `patch added alias` includes both declaration and scanned tail in the classification region |
| Context or added `x = b->mapped_` ending in a splice, then removed `old;`, then added `mem;` | Refused | `patch removed splice` and `patch added removed splice` join only new-side lines; the removed position stays blank in output |
| Spliced mapped token separated by a no-newline marker | Refused | `patch marker splice` excludes the marker before lexing; `marker splice origin` checks exact output and origins |
| Spliced mapped token separated by removed `-- old;` | Refused | `removed decrement splice` consumes a removed decrement from the old count without interrupting the new-side splice |
| Context `if (ok)` ending in a splice, followed by an added cast read and context `do_something();` | Refused | `patch context splice read` retains added provenance on the joined context line |
| Added or context `memcpy(dst,` in one hunk and added `b->mapped_mem, n);` in the next | Refused | `patch hunk call` and `patch hunk context call` prevent the call contract from crossing unknown text |
| A call prefix in a completed hunk before paired file headers, followed by an added mapped argument in a new hunk | Refused | `patch file call` keeps each hunk independent across file metadata |
| Added alias declaration whose closing brace occurs only in the next hunk, or is absent | Refused | `patch hunk alias` and `patch open added alias` fail closed at the declaration's region end |
| Allocation prefix in one hunk and its storage/host-only arguments in the next | Unreadable-call error | `patch hunk allocation` requires this diagnostic, not merely a storage-buffer error |
| Device-local flag assignment in an earlier hunk, followed by an added allocation using that name | Unresolvable-flags error | `patch hunk flags` rejects earlier-region flag resolution |
| Context-only reader or open alias in a different hunk from an addition | Unreported | `patch hunk context reader` and `patch hunk context alias` keep overlap local to each region |
| `memcpy(b->mapped_mem, src, n];` and `memcpy(b->mapped_mem, src, n};` | Refused | `bracket call closer` and `brace call closer` require a closing parenthesis, rather than any delimiter returning depth to zero |
| `pel_mc_build_predictors(h, (S){a, b}.w, n, (int32_t *)b->mapped_mem, &gx, &gy);` | Refused | `compound literal sink` records the lexical limit: `enclosing_call()` stops at `}`, despite the mapped pointer occupying the write-only slot |
| Removed `-- i;` and added `++ i, x = b->mapped_mem[0];` in `@@ -1 +1 @@` | Refused | `patch header-shaped read` consumes the apparent file-header pair as body lines |
| Context `reader(`, removed `-- i,`, added `++ i, b->mapped_mem);` | Refused | `patch header-shaped argument` retains the added mapped argument |
| Removed `-- i;`, added `++ i;`, then added `x = b->mapped_mem[0];` | Refused | `patch header-shaped then read` retains the later addition |
| Empty context line before an added mapped read | Refused without a parse error | `patch empty context` consumes the empty line from both counts; `empty context` asserts exact output and origins |
| Added mapped read after a complete `@@ -1 +1 @@` body | Unreadable-metadata error | `patch trailing addition` and `trailing addition` reject the extra addition at line 4 |
| EOF with remaining counts, including a header at EOF | Unreadable-hunk error | `patch incomplete`, `patch EOF header`, `incomplete body` and `header at EOF` |
| Unknown body prefix or a count below zero | Unreadable-hunk error | `patch unknown prefix`, `patch negative count`, `unknown prefix`, `negative old count` and `negative new count` |
| Malformed `@@` after a completed hunk, or a patch with no hunk | Unreadable-hunk error | `patch malformed header`, `patch no hunk`, `malformed header` and `no hunk`; the completed prefix isolates malformed-header rejection from the no-hunk rule |
| `@@` inside an incomplete body | Unreadable-hunk error | `nested header` cannot open a new hunk while counts remain |
| Zero-count hunk without a body, or a section label after a valid header | Accepted | `zero counts` and `empty context` cover the boundary forms; an outside-body addition is refused |
| Lone CR, CRLF or backslash-CR in C/H or an added patch line | Unreadable-control error | `_reader_cases()` requires line 2 for embedded hazards; `CRLF patch` requires line 1 |
| Invalid UTF-8 in `.patch`, `.c` or `.h`; NUL, form feed, ESC, DEL and other tested controls | Unreadable-source/patch error | Strict decoding and the character allow-list retain the physical LF line number |
| `??/` splice, `'??/''` quote escape, `??=` or `??(` | Unreadable-trigraph error | `_reader_cases()` tests C, headers and patch additions before comment/literal masking |
| `GIT binary patch`, `literal 40`, rename/copy/similarity/mode metadata, mail headers, malformed `diff`/`index`, or `+++ /dev/null` | Unreadable-metadata error | `BAD_METADATA` asserts line 1; binary sections cannot bypass the scan |
| Blank between hunks, addition after a completed hunk, or marker before a hunk | Unreadable-metadata error | Exact line-number assertions in `_patch_error_self_test()` |
| Tab-indented addition, non-ASCII UTF-8 comment and `"??x"` literal | Accepted | `safe text` retains each boundary in C, headers and patches; `live metadata` keeps an indented addition |
| Full `diff --git`, `index` with and without mode, `new file mode`, `--- /dev/null`, `+++ b/` | Accepted | `live metadata` asserts exact text and added line 8; `metadata between hunks` keeps file headers outside lexical input |
| Completed hunk, adjacent no-newline marker, then a second hunk containing a mapped read | Read refused at its added line; no unreadable error | `completed marker then read` asserts exact masked text and origins `{2, 5}`; the full checker integration asserts the second-hunk read at `c.patch:9` and no extra error |
| Digraph subscript or alias read; UCN or UTF-8 alias; `#define` or `%:define` mapped read | Refused | `digraph subscript`, `digraph alias`, `UCN alias`, `UTF-8 alias`, `macro body`, `digraph macro` retain the mapped token without translation or expansion |
| `vf_pelorus_analyze_vulkan.c:710` | Known, #596 | `base` feeds per-tile reads; cost scales with resolution |
| `vf_pelorus_denoise_vulkan.c:814` | Known, #596 | `acc_out` exposes a fixed 160-word accumulator |
| `vf_pelorus_grain_estimate_vulkan.c:907` | Known, #596 | `attach_estimate()` reads a fixed statistics struct; collapsed statement includes its preceding `if (err >= 0)` |

The scanner is lexical, not a C parser, and performs no preprocessing or macro expansion. Unterminated literals and block comments remain unmasked; C23 `1'000` is treated as an unterminated character literal. Comments retain their width and newlines; splices defer swallowed newlines to the next surviving newline or EOF. The scanner does not follow pointers obtained through `vk->MapMemory` or wrapper implementations. `--files DIR` redirects both source checks to a copy for mutation verification. An input tree with no `mapped_mem` token fails closed.

## Verification

`patch_regions()` reads each hunk by its declared old/new counts and produces an independent masked text, added-origin set and blank prefix offset for diagnostics. Omitted counts default to one. Space-prefixed and empty body lines consume both counts, removals consume only the old count, additions consume only the new count, and backslash markers consume neither. Header-shaped lines inside a body remain ordinary body lines. A body ends when both counts reach zero. Unknown body prefixes, negative counts, incomplete bodies, malformed outside-body `@@` headers and patches with no hunk fail closed. `source_texts()` retains a `PatchError` for the unreadable patch; `check()` emits its single `file:line: unreadable patch hunk ...` diagnostic, and the CLI prints it and exits 1. Only actual new-side context and additions enter lexing, joined directly in order; removals and markers are restored as blank output positions afterward. Splice origins follow the previous new-side line rather than the previous patch line. Outside-body lines must fully match the `c_source.py` allow-list: paired `diff --git` paths; hexadecimal `index` with optional octal mode; `new file mode`; old `a/` or `/dev/null` header; new `b/` header; counted hunk header; or a backslash-space marker immediately following a completed body. Binary, rename, mail and other unknown shapes fail closed, as do blank lines and trailing additions. A `GIT binary patch` section can write content, so outside-body text cannot be ignored wholesale. Strict UTF-8 decoding and LF-only splitting precede parsing and masking; only non-control characters, LF and tab are permitted, and trigraphs fail closed before comments or literals can hide them. Source loading and patch parsing live in `c_source.py`; lexing lives in `c_scan.py`. No statement, call, argument, alias tail or flag lookup crosses a region. `source_texts()` supplies those regions independently; `patch_source()` combines them only for lexical fixtures. The shared `argument_spans()` scanner tracks parentheses, brackets and braces; `call_args()` and `argument_index()` derive their results from it. A comma separates arguments only at depth one, and only a closing parenthesis returning depth to zero ends the call. The pointer-expression helper counts mapped tokens before stripping casts. Refused mapped statements require overlap from statement start through statement end, extended through the scanned alias tail; a missing closing brace fails closed through the available region end. Allocation calls require overlap through the closing parenthesis, or through the available bounded call scan when unreadable. Exact scanner tests assert masked text, line count and added origins.

Run the focused checker from the repository root:

```bash
python3 -I scripts/check-vulkan-buffer-domain.py --self-test
python3 -I scripts/check-vulkan-buffer-domain.py
ruff check --no-cache --config ruff.toml scripts/check-vulkan-buffer-domain.py scripts/c_scan.py scripts/c_source.py
ruff format --no-cache --check --config ruff.toml scripts/check-vulkan-buffer-domain.py scripts/c_scan.py scripts/c_source.py
```

The live tree and self-tests exit 0. The supplied inventory of 34 scanned files has only LF, no control characters or tabs, no `??` or digraph-shaped sequences, and 58 lines with non-ASCII UTF-8 in comments. The strict reader accepts the live tree without exceptions. File integration uses `TemporaryDirectory` with `a.c` (valid storage allocation and copy-out), `b.h` (cast read), `c.patch` (ignored context read, added read at line 5, host-only storage allocation at line 6, second-hunk added read at line 9 after a no-newline marker), and the three unchanged known-read fixtures. It requires exactly those four new diagnostics and no empty-scan failures. The additional patch fixtures pass through `source_texts()` and full `check()` calls using real patch files plus valid allocation and known-read baselines. They assert mapped-use and allocation diagnostics separately, including unreadable calls, unresolvable flags and exactly one error for each malformed patch. The original hand-maintained patch, increment, reader and comment-close fixtures now run through this integration path with count-consistent headers. Self-tests cover bulk copies and stores, unsafe calls and aliases, patch origin boundaries, exact exception coverage, unknown categories, an empty scan, expiry at day 121 and acceptance at day 120. Standalone mapped-case diagnostics are also checked for doubled semicolons. The pinned Ruff (`ruff==0.17.3` in `.config/python-lint/requirements.txt`) reports clean lint and formatting.

### Git apply boundaries

Measured with Git 2.56.0 in scratch directories using plain `git apply p.diff`, with file headers `--- a/x.c` and `+++ b/x.c`. These are the supplied judge measurements; no Git application was rerun in this worktree. The applied patch path is used by `ffmpeg-patches/generate.sh` via `git -C "$WORKTREE" apply`.

| Hunk body | Measured result |
| --- | --- |
| `@@ -1 +1 @@\n--- i;\n+++ i, x = b->mapped_mem[0];`, old file `-- i;` | Exit 0; new file `++ i, x = b->mapped_mem[0];` |
| `@@ -1,3 +1,3 @@\n a\n\n-b\n+c` | Exit 0; empty line is context |
| `@@ -1,2 +1,2 @@\n-a\n+z`, then EOF | Exit 128; `corrupt patch at p.diff:6` |
| `@@ -1,2 +1,2 @@\n a\n*b`, then remaining body | Exit 128; `corrupt patch at p.diff:5` |
| `@@ -1 +1 @@\n-a\n+z\n+junk` | Exit 0; new file `z`; trailing addition ignored |
| `@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+z` | Exit 0; marker consumes neither count |
| New-file addition `+a;` CR space `x = b->mapped_mem[0];` | Exit 0; writes the read; lone CR ends a C line |
| New-file addition `+x = b->mapped_\` CR `mem[0];` | Exit 0; writes the read; C splices backslash-CR |
| New-file `GIT binary patch` / `literal N` containing C with a mapped read | Exit 0; writes the read from the binary payload |

### Compiler translation boundaries

The following supplied measurements used baseline `56ba1af`, Git 2.56.0, GCC 16.2.1 and clang 23.1.4 with `-std=c17 -E -P`. Both compilers produced the stated result. The three new patch rows above used the same baseline and Git version. These tool runs were supplied measurements, not rerun here. FFmpeg `n9.0.1` configure line 4412 sets `stdc_default="c17"`; C23's removal of trigraphs therefore does not remove this input hazard.

| Input | Measured compiler result | Checker on `56ba1af` / current reader |
| --- | --- | --- |
| `int x = b->mapped_??/` LF `mem[0];` | `int x = b->mapped_mem[0];` | Accepted / unreadable trigraph |
| `char c = '??/''; int x = b->mapped_mem[0];` | Character literal becomes `'\''`; mapped read remains code | Accepted / unreadable trigraph |
| `mapped` NUL `_mem` | Two tokens, with no mapped read | No read / unreadable control character |

Trigraph translation occurs before comment and literal recognition. Lone CR is a C line boundary even though patch lines split only at LF. Digraph subscripts and alias reads, UCN/UTF-8 aliases and both `#define` spellings already stayed refused on the measured baseline: the mapped token remained visible. The checker still does not translate digraphs or UCNs or expand macros, and its new plants preserve these refusals. CRLF sources fail closed.

### Self-test strength

All 77 non-equivalent rule mutants below exited 1 on 2026-11-11. Copy all three Python files into `$TMPDIR/<name>/scripts/`, make one exact replacement, and run `python3 -I <copy>/check-vulkan-buffer-domain.py --self-test --files ffmpeg-patches/files` from the repository root. Each replacement is represented by exact Python string literals, including indentation and escaped newlines; require exactly one occurrence of `old` across all three files before `text.replace(old, new, 1)` in the named file. The table also records three equivalent mutants and two harness removals with expected exit 0.

All 65 previous table rows were rerun against the final files, together with the 13 reader/metadata rule mutants. Moved parser and reader rows now name `c_source.py`. The replacement rows `hunk-reset-off` and `body-past-counts` now call `patch_segment`, replacing the removed `_patch_segment` spelling. The retained `new-header-previous-off` and `excluded-lines-lexed` replacements still match the new-side selector, including empty context lines. `malformed-header-metadata` is now equivalent for acceptance: returning no hunk header leaves the same line for the strict metadata allow-list to reject, with a different unreadable-error detail.

The new reader and metadata plants were run with their checks disabled through the corresponding mutants below; every non-equivalent removal failed. `body-past-counts` initially survived after trailing additions became unreadable; the new `metadata between hunks` exact-output fixture exposes its accidental inclusion of the following file header. Replaying that mutant fails the new fixture. Earlier verification established failing header-shaped reads, subscript, nested-pointer, patch-context, open-alias, leading-whitespace, excluded-line splice and cross-hunk fixtures before their respective fixes.

| Mutant | File | Exact replacement (`old` → `new`) | Observed exit | First failing self-test case |
| --- | --- | --- | --- | --- |
| classifier-off | `check-vulkan-buffer-domain.py` | `'if not allowed and added_overlap(text, start, end, added):'` → `'if False:'` | 1 | `mapped case failed: digraph subscript` |
| store-lookahead | `check-vulkan-buffer-domain.py` | `'=(?!=)"'` → `'="'` | 1 | `mapped case failed: write alias equality` |
| single-mapped | `check-vulkan-buffer-domain.py` | `'if len(re.findall(r"\\bmapped_mem\\b", statement)) != 1:'` → `'if False:'` | 1 | `mapped case failed: store with mapped RHS` |
| const-rule | `check-vulkan-buffer-domain.py` | `'if not alias or re.search(r"\\bconst\\b", alias[1] + " " + alias[3]):'` → `'if not alias:'` | 1 | `mapped case failed: const alias store` |
| pointer-expression | `c_scan.py` | `'return re.fullmatch(chain, plain) is not None'` → `'return True'` | 1 | `mapped case failed: offset` |
| parenthesis-merge | `c_scan.py` | `'    if re.search(r"[\\w)\\]]\\s*\\(", plain):\n        return False\n'` → `''` | 1 | `mapped case failed: parenthesised reader` |
| expiry | `check-vulkan-buffer-domain.py` | `'today > expiry'` → `'today >= expiry'` | 1 | `known-read case failed: boundary` |
| sink-index | `check-vulkan-buffer-domain.py` | `'index == SINKS.get(name, (-1, ""))[0]'` → `'name in SINKS'` | 1 | `mapped case failed: subscript sink input` |
| mapped-fail-closed | `check-vulkan-buffer-domain.py` | `'if mapped_total == 0:'` → `'if False:'` | 1 | `empty tree accepted: no mapped_mem use found` |
| duplicate-sites | `check-vulkan-buffer-domain.py` | `'if count != 1:'` → `'if count == 0:'` | 1 | `known-read case failed: duplicate` |
| alias-uses | `c_scan.py` | `'    allowed = all(\n        re.match(store, tail[use.end() :]) and statement_start(tail, use.start())\n        for use in uses\n    )'` → `'    allowed = True'` | 1 | `mapped case failed: digraph alias` |
| semicolon | `check-vulkan-buffer-domain.py` | `"{statement.removesuffix(';')}"` → `'{statement}'` | 1 | `doubled semicolon: digraph subscript` |
| mask-identity | `c_scan.py` | `'    text = _splice(text)'` → `'    return text\n    text = _splice(text)'` | 1 | `mapped case failed: quoted brace alias` |
| literals-off | `c_scan.py` | `'elif text[pos] in "\\"\'":'` → `'elif False:'` | 1 | `mapped case failed: quoted brace alias` |
| splices-off | `c_scan.py` | `'    text = _splice(text)'` → `'    text = text'` | 1 | `mapped case failed: spliced token` |
| unterminated-unmask-off | `c_scan.py` | `'return text[start:pos], pos'` → `'return text[start] + " " * (pos - start - 1), pos'` | 1 | `C masking case failed: unterminated character` |
| patch-context-off | `c_scan.py` | `'line.startswith(("+", " "))'` → `'line.startswith("+")'` | 1 | `C patch case failed: removed decrement splice` |
| member-guard-off | `c_scan.py` | `'("" if member else name[1])'` → `'name[1]'` | 1 | `mapped case failed: member dot source` |
| bulk-statement-off | `check-vulkan-buffer-domain.py` | `'if name in BULK and index == 0:'` → `'if False:'` | 1 | `mapped case failed: memset result reader` |
| type-allowlist-off | `check-vulkan-buffer-domain.py` | `'({TYPE}(?:\\s+{TYPE})*)'` → `'(\\w+(?:\\s+\\w+)*)'` | 1 | `mapped case failed: return output` |
| alias-statement-off | `c_scan.py` | `' and statement_start(tail, use.start())'` → `''` | 1 | `mapped case failed: chained alias store` |
| duplicate-entry-off | `check-vulkan-buffer-domain.py` | `'if key in seen:'` → `'if False:'` | 1 | `known-read case failed: duplicate entries` |
| bulk-suffix-off | `check-vulkan-buffer-domain.py` | `'return prefix in ("", "(void)") and suffix == ";"'` → `'return prefix in ("", "(void)")'` | 1 | `mapped case failed: memcpy result suffix` |
| statement-close-paren-off | `c_scan.py` | `'";{})"'` → `'";{}"'` | 1 | `mapped case failed: write alias` |
| statement-close-brace-off | `c_scan.py` | `'";{})"'` → `'";{)"'` | 1 | `mapped case failed: write alias` |
| statement-else-do-off | `c_scan.py` | `'bool(re.search(r"\\b(?:else\|do)$", prefix))'` → `'False'` | 1 | `mapped case failed: write alias` |
| type-uppercase-off | `check-vulkan-buffer-domain.py` | `'\|[A-Z]\\w*'` → `''` | 1 | `mapped case failed: alias first store` |
| type-tag-off | `check-vulkan-buffer-domain.py` | `'\|(?:struct\|union\|enum)\\s+\\w+'` → `''` | 1 | `mapped case failed: alias type groups` |
| type-scalars-off | `check-vulkan-buffer-domain.py` | `'const\|volatile\|restrict\|signed\|unsigned\|short\|long\|int\|char\|float\|double\|void\|_Bool'` → `'const'` | 1 | `mapped case failed: alias type groups` |
| block-nesting-off | `c_scan.py` | `'if text[pos] == "{":'` → `'if False:'` | 1 | `mapped case failed: nested block alias escape` |
| literal-newline-off | `c_scan.py` | `'        if char == "\\n":\n            return text[start:pos], pos\n'` → `''` | 1 | `C masking case failed: literal newline stop` |
| hunk-reset-off | `c_source.py` | `'masked, added = patch_segment(lines[start : i + 1])'` → `'masked, added = patch_segment(lines[: i + 1])\n                masked, added = masked[start:], {n - start for n in added if n > start}'` | 1 | `C patch case failed: segment reset` |
| statement-empty-prefix-off | `c_scan.py` | `'not prefix or prefix[-1]'` → `'prefix and prefix[-1]'` | 1 | `mapped case failed: alias first store` |
| statement-at-close-brace-off | `c_scan.py` | `'for delimiter in ";{}"'` → `'for delimiter in ";{"'` | 1 | `mapped case failed: store after block` |
| new-header-previous-off | `c_scan.py` | `'if not line or line.startswith(("+", " "))'` → `'if not line or (line.startswith(("+", " ")) and not line.startswith("+++ "))'` | 1 | `C patch case failed: added pre-increment` |
| call-bracket-depth-off | `c_scan.py` | `'if char in "([{":\n            depth += 1\n        elif char in ")]}":'` → `'if char in "({":\n            depth += 1\n        elif char in ")}":'` | 1 | `mapped case failed: subscript sink input` |
| call-brace-depth-off | `c_scan.py` | `'if char in "([{":\n            depth += 1\n        elif char in ")]}":'` → `'if char in "([":\n            depth += 1\n        elif char in ")]":'` | 1 | `C argument case failed: (S){a, b, c}.p` |
| pointer-single-token-off | `c_scan.py` | `'if len(re.findall(rf"\\b{re.escape(token)}\\b", arg)) != 1:'` → `'if False:'` | 1 | `mapped case failed: nested mapped source` |
| patch-additions-only | `c_scan.py` | `'return out, added'` → `'return [line if i + 1 in added else "" for i, line in enumerate(out)], added'` | 1 | `C patch case failed: context closes comment` |
| splice-origin-off | `c_scan.py` | `'added.add(origin + 1)'` → `'added.add(i + 1)'` | 1 | `C patch case failed: removed decrement splice` |
| alias-region-tail-off | `c_scan.py` | `'return allowed, start + len(tail)'` → `'return allowed, start'` | 1 | `patch case failed: patch context alias` |
| mapped-origin-off | `check-vulkan-buffer-domain.py` | `'if not allowed and added_overlap(text, start, end, added):'` → `'if not allowed:'` | 1 | `source tree case failed` |
| allocation-additions-only | `check-vulkan-buffer-domain.py` | `'    """Return (errors, number of calls read) for one source text."""'` → `'    """Return (errors, number of calls read) for one source text."""\n    text = "\\n".join(line if added is None or i + 1 in added else "" for i, line in enumerate(text.split("\\n")))'` | 1 | `patch case failed: patch split allocation` |
| allocation-origin-off | `check-vulkan-buffer-domain.py` | `'if not added_overlap(text, match.start(), end, added):'` → `'if False:'` | 1 | `patch case failed: patch context allocation` |
| statement-leading-space | `c_scan.py` | `'    start += len(text[start:pos]) - len(text[start:pos].lstrip())\n'` → `''` | 1 | `patch case failed: patch reader after addition` |
| alias-open-region-off | `c_scan.py` | `'return False, len(text)'` → `'return False, start'` | 1 | `patch case failed: patch open alias` |
| excluded-lines-lexed | `c_scan.py` | `'    positions = [\n        i for i, line in enumerate(lines) if not line or line.startswith(("+", " "))\n    ]\n    side = [lines[i][1:] for i in positions]'` → `'    positions = list(range(len(lines)))\n    side = [line[1:] if line.startswith(("+", " ")) else "" for line in lines]'` | 1 | `C patch case failed: removed decrement splice` |
| splice-origin-patch-order | `c_scan.py` | `'side[i - 1]'` → `'lines[pos - 1][1:]'` | 1 | `C patch case failed: removed decrement splice` |
| region-barrier-off | `c_source.py` | `'patch_regions(raw)'` → `'[patch_source(raw)]'` | 1 | `patch case failed: patch hunk call` |
| alias-region-end-accepted | `c_scan.py` | `'return False, len(text)'` → `'return True, len(text)'` | 1 | `patch case failed: patch open alias` |
| flag-resolution-across-regions | `c_source.py` | `'"\\n" * start + "\\n".join(masked)'` → `'((regions[-1][0] + "\\n" * (start - regions[-1][0].count("\\n"))) if regions else "\\n" * start) + "\\n".join(masked)'` | 1 | `patch case failed: patch hunk call` |
| close-paren-only-off | `c_scan.py` | `'if depth == 0 and char == ")":'` → `'if depth == 0:'` | 1 | `mapped case failed: bracket call closer` |
| missing-count-zero | `c_source.py` | `'else 1 for i in (2, 4)'` → `'else 0 for i in (2, 4)'` | 1 | `C patch case failed: metadata between hunks` |
| context-new-only | `c_source.py` | `'old, new = old - 1, new - 1'` → `'old, new = old, new - 1'` | 1 | `C patch case failed: empty context` |
| removed-new-count | `c_source.py` | `'old -= 1'` → `'new -= 1'` | 1 | `C patch case failed: removed decrement splice` |
| added-old-count | `c_source.py` | `'new -= 1'` → `'old -= 1'` | 1 | `C patch case failed: metadata between hunks` |
| empty-context-off | `c_source.py` | `'if prefix in ("", " "):'` → `'if prefix == " ":'` | 1 | `C patch case failed: empty context` |
| open-hunk-accepted | `c_source.py` | `'    if old or new:\n        raise PatchError(f"{len(lines) + 1}: unreadable patch hunk at EOF")'` → `'    if False:\n        raise PatchError(f"{len(lines) + 1}: unreadable patch hunk at EOF")'` | 1 | `C source case failed: incomplete body` |
| negative-count-accepted | `c_source.py` | `'if min(old, new) < 0:'` → `'if False:'` | 1 | `C source case failed: negative old count` |
| unknown-prefix-ends | `c_source.py` | `'raise PatchError(f"{number}: unreadable patch hunk body prefix")'` → `'return 0, 0'` | 1 | `C source case failed: unknown prefix` |
| malformed-header-metadata | `c_source.py` | `'raise PatchError(f"{number}: unreadable patch hunk header")'` → `'return None'` | 0 | `None; expected 0` |
| no-hunk-accepted | `c_source.py` | `'if not seen:'` → `'if False:'` | 1 | `C source case failed: no hunk` |
| header-shape-readded | `c_source.py` | `'        if old or new:\n            old, new = _consume_line(line, old, new, i + 1)'` → `'        if old or new:\n            if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):\n                old = new = 0\n                continue\n            old, new = _consume_line(line, old, new, i + 1)'` | 1 | `patch case failed: patch header-shaped read` |
| body-past-counts | `c_source.py` | `'masked, added = patch_segment(lines[start : i + 1])'` → `'masked, added = patch_segment(lines[start : next((j for j in range(i + 1, len(lines)) if lines[j].startswith("@@")), len(lines))])'` | 1 | `C patch case failed: metadata between hunks` |
| patch-error-drop | `check-vulkan-buffer-domain.py` | `'errors.append(f"{name}:{text}")'` → `'pass'` | 1 | `patch case failed: patch trailing addition` |
| universal-newlines | `c_source.py` | `'raw = data.decode("utf-8")'` → `'raw = path.read_text(encoding="utf-8")'` | 1 | `C source case failed: CRLF patch` |
| control-check-off | `c_source.py` | `'if any(unicodedata.category(c) == "Cc" and c not in "\\t" for c in line):'` → `'if False:'` | 1 | `C source case failed: CRLF patch` |
| CR-allowed | `c_source.py` | `'c not in "\\t"'` → `'c not in "\\t\\r"'` | 1 | `C source case failed: CRLF patch` |
| NUL-allowed | `c_source.py` | `'c not in "\\t"'` → `'c not in "\\t\\0"'` | 1 | `C source case failed: '\x00'` |
| decode-replace | `c_source.py` | `'data.decode("utf-8")'` → `'data.decode("utf-8", errors="replace")'` | 1 | `C source case failed: invalid UTF-8` |
| decode-skip | `c_source.py` | `'texts.append((path.name, error, None))'` → `'if "UTF-8" not in str(error):\n                    texts.append((path.name, error, None))'` | 1 | `C source case failed: invalid UTF-8` |
| trigraph-off | `c_source.py` | `'if re.search(r"\\?\\?[=(/)\'<!>-]", line):'` → `'if False:'` | 1 | `C source case failed: x = b->mapped_??/` |
| trigraph-slash-only | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?/"'` | 1 | `C source case failed: // ??=` |
| metadata-off | `c_source.py` | `'elif not _patch_metadata(line, i == marker):'` → `'elif False:'` | 1 | `C source case failed: trailing addition` |
| metadata-prefix | `c_source.py` | `'re.fullmatch(shapes, line)'` → `'re.match(shapes, line)'` | 1 | `C source case failed: diff --git a/x.c b/x.c junk` |
| index-any-text | `c_source.py` | `'index [0-9a-f]+\\.\\.[0-9a-f]+(?: [0-7]{6})?'` → `'index .*'` | 1 | `C source case failed: index zz..yy` |
| marker-anywhere | `c_source.py` | `'(marker and line.startswith("\\\\ "))'` → `'line.startswith("\\\\ ")'` | 1 | `C source case failed: marker before hunk` |
| new-header-dev-null | `c_source.py` | `'\\+\\+\\+ b/\\S+'` → `'\\+\\+\\+ (?:b/\\S+\|/dev/null)'` | 1 | `C source case failed: +++ /dev/null` |
| patch-segment-CR-optional | `c_scan.py` | `'r"\\\\[ \\t]*$"'` → `'r"\\\\[ \\t]*\\r?$"'` | 0 | `None; expected 0` |
| source-harness-off | `check-vulkan-buffer-domain.py` | `' + reading_self_test()'` → `''` | 0 | `None; expected 0` |
| scan-harness-off | `check-vulkan-buffer-domain.py` | `' + scan_self_test()'` → `''` | 0 | `None; expected 0` |
| keyword-skip | `c_scan.py` | `'if name:'` → `'if name and name[1] not in {"if", "while", "for", "switch", "return", "sizeof"}:'` | 0 | `None; expected 0` |

Some mutants first emit diagnostics from the live tree; the table identifies their first failing self-test case. The classifier-off mutant first emits a stale analyze exception, then fails `digraph subscript`. The mask-identity mutant first emits a false mapped-read diagnostic for comment text at `vf_pelorus_analyze_vulkan.c:632`, then fails `quoted brace alias`. The earlier-region flag mutant fails `patch hunk call` and independently fails `patch hunk flags`. Member-source, context-overlap, allocation-overlap and exact-origin fixtures retain their independent checks.

Equivalent mutants have expected exit 0. `malformed-header-metadata` leaves the rejected header to the metadata allow-list; its diagnostic still names the same unreadable line. `patch-segment-CR-optional` restores unreachable CR matching: the reader fixtures reject CR before `patch_segment()` runs, while `mask_c()` retains direct-string CR handling. `keyword-skip` preserves refusal through the pointer-expression guard and statement allow-list: within bulk/sink arguments, keyword parentheses remain invalid pointer expressions; at statement level, only plain cast stores and allow-listed alias declarations pass.

The two harness removals also have expected exit 0. Dropping `reading_self_test()` or `scan_self_test()` removes its assertions while leaving runtime checks intact; a clean live tree therefore cannot detect that missing test call. These are harness observations, not killed rule mutants.

### Source mutations

For mutations, copy `ffmpeg-patches/files` into a temporary directory and run `python3 -I scripts/check-vulkan-buffer-domain.py --files <copy>` after each independent change:

| Mutation | Observed result (rerun 2026-11-11) |
| --- | --- |
| m1: Replace `attach_motion(s, in, s->mv_host,` with `attach_motion(s, in, ((const FFVkBuffer *)mvx_ref->data)->mapped_mem,` | Exit 1; mapped-read diagnostic at `vf_pelorus_mc_vulkan.c:808` |
| m2: Append `+ 0` to the analyze known-read expression | Exit 1; new refused site and stale exception (`matches no site; remove the entry`) |
| m3: Remove the analyze entry from a temporary script copy with `c_scan.py` and `c_source.py` beside it; run that script with `--files` pointing at the real source tree | Exit 1; mapped-read diagnostic at `vf_pelorus_analyze_vulkan.c:710` |
| m4: Point `--files` at a directory containing only `x.c` with `int x;` | Exit 1; both empty-scan diagnostics name the selected directory, not the default |

These checks leave runtime sources and the FFmpeg patch stack unchanged. `REUSE.toml` supplies the existing EUPL-1.2 annotation for the new Markdown files.

### Review findings

| Finding | Fix | Planted case |
| --- | --- | --- |
| F1: Literal braces and comment markers alter classification | Shared literal/comment masking; malformed constructs retain source | Quoted/character brace aliases, quoted comments, literal mapped token, unterminated character |
| F2: Member callee inherits a bare-function contract | Refuse names preceded by `->` or `.` | Member destination, source and sink |
| F3: Spliced token disappears | Join backslash-newline before masking; restore newline count | Spliced mapped token, CRLF/whitespace and multiple-splice lexical cases |
| F4: Duplicate exception entries pass | Reject repeated `(file, statement)` keys | Duplicate entries with one refused site |
| F5: Bulk return value escapes | Require destination bulk call to be the whole expression statement | Nested result reader, result alias, assignment and condition |
| F6: Keyword accepted as an alias type | Allow-list declaration specifiers and type-name forms | Return/else/do output stores |
| F7: Alias store appears within an expression | Require every use to start a statement | Chained store, call argument, RHS/sum/index reads |
| F8: File-selection behavior lacks integration coverage | Exercise C, header and patch files through `source_texts()` and `check()` | Temporary tree requires exactly `b.h:1`, `c.patch:5`, `c.patch:6` and `c.patch:9` diagnostics |
| Copilot on `9ccfe96`, `c_scan.py:93`: subscript commas shift argument positions | Derive both splitting and indexing from one delimiter-depth scan | Subscript sink input, accepted subscript copy source, C/patch subscript allocations, brace sink input and exact argument spans |
| Copilot on `9ccfe96`, `c_scan.py:162`: context blanking changes classification | Keep the full masked new side with splice-aware origins; require statement/tail or allocation-call overlap | Result escape, spliced reader, context alias/open alias, split allocation, context-only reader/allocation, reader argument and added alias |
| Copilot on `9ccfe96`, `check-vulkan-buffer-domain.py:188`: cast stripping hides nested mapped access | Require exactly one mapped token in each allowed bulk/sink pointer expression | Nested mapped source, destination and sink; existing nested cast store remains refused |
| CodeRabbit outside-diff finding on `9ccfe96`, `docs/research/0458-mapped-read-guard.md:151`: a context-line splice absorbs an added read | Carry added provenance to the joined new-side origin, skipping removed lines and markers | `patch context splice read`: context `if (ok)` ending in a splice, added cast read, context `do_something();` |
| Header-shaped removed/added pair terminates a hunk before an added mapped read | Consume declared old/new counts; metadata shapes have no meaning inside a body; report malformed hunks | `patch header-shaped read`, `patch header-shaped argument`, `patch header-shaped then read`; `header-shape-readded` exits 1 |
| Lone CR and backslash-CR inside a patch addition bypass LF-based masking | Strict reader rejects CR before patch parsing or lexing | Embedded CR, CRLF and CR-splice cases assert the unreadable line in each source kind |
| Binary patch sections write unseen C content | Full-line outside-body allow-list rejects binary and other unsupported metadata | `GIT binary patch`, `literal 40`, prefix-match and index-shape plants |
| Trigraph splice joins a mapped token before C lexing | Reject all nine trigraph forms before masking | `??/` splice in C, header and patch addition; non-splice `??=` and `??(` |
| Trigraph escape changes literal termination and exposes a read | Reject trigraphs in literals and comments too | `'??/''` followed by a mapped read in all source kinds |

Rule/source mutation evidence checked at `2026-11-11T06:30:16Z`. NLOC excludes blank and `#` comment lines and includes docstrings. Decision counts include branches, loops, comprehension clauses and filters, conditional expressions, exception handlers and Boolean operators.

| File | SHA-256 | NLOC | Lines over 100 characters | Maximum function lines / decisions |
| --- | --- | --- | --- | --- |
| `check-vulkan-buffer-domain.py` | `1d8d7015595a54892bc496f7ebec41f864afe7042af54b0a400f5e7c3ad8be39` | 500 | 45 | 30 / 10 |
| `c_scan.py` | `b9714d1f971b90848ff16222a966020b7480efa00fa8ad6dc2e956f84065e206` | 269 | 0 | 45 / 9 |
| `c_source.py` | `a3eb161cc76bafa57a461a6d59b9716f703243553536f62e4756fb42b325a068` | 298 | 0 | 39 / 10 |

All three files remain within 500 NLOC, 60 physical lines per function and 10 decision points per function. The checker's 45 pre-existing lines over 100 characters are unchanged; neither supporting module has an overlength line.
