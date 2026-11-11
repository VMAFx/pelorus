<!-- markdownlint-disable MD013 MD060 -->
# Mapped-memory host-read regression guard

**Date:** 2026-11-11

**Decision:** [ADR-0255](../adr/0255-mapped-memory-host-reads.md).

**Scope:** `scripts/check-vulkan-buffer-domain.py`, fast test `vulkan-buffer-domain`, reader baseline `56ba1af`, issues #458 and #596. Source evidence comes from C/H files and the masked new side of `ffmpeg-patches/files/*.patch`; line splicing precedes comment/literal masking. Each patch hunk is classified independently with context visible; statement and allocation diagnostics require added-line overlap, while directive refusals include context lines. The default scan reads only `ffmpeg-patches/files/`, excluding the shared FFmpeg fix series applied first by `scripts/fetch-ffmpeg-series.sh`.

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
| Context `T *p=(T*)(b->mapped_mem);` or `T *p=(T*)(b)->mapped_mem;` followed by added `x=p[0];` | Refused | `patch grouped context alias` and `patch grouped base alias` drop grouping parentheses before naming the binding, so the scanned block tail still covers the addition |
| Context allocation prefix through the fifth argument, followed by added storage and host-only flags | Storage-buffer error | `patch split allocation` classifies the complete call and checks overlap through its closing parenthesis |
| Context `x = reader(b->mapped_mem);` with unrelated added `y = 1;` before or after it; context calls `(reader)(b->mapped_mem)`, `fns[0](b->mapped_mem)` and `reader (b->mapped_mem)` before the addition | Unreported | `patch context reader` and `patch reader after addition` exclude context-only statements and leading whitespace from overlap; an opening parenthesis after a name, `)` or `]` stays a call, so none of the four calls binds an alias |
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
| Every trigraph form, including the `??/` splice, the `'??/''` quote escape and the `??'` quote | Unreadable-trigraph error | `_reader_cases()` tests C, headers and patch additions before comment/literal masking |
| `GIT binary patch`, `literal 40`, rename/copy/similarity/mode metadata, mail headers, malformed `diff`/`index`, or `+++ /dev/null` | Unreadable-metadata error | `BAD_METADATA` asserts line 1; binary sections cannot bypass the scan |
| Blank between hunks, addition after a completed hunk, or marker before a hunk | Unreadable-metadata error | Exact line-number assertions in `_patch_error_self_test()` |
| Tab-indented addition, non-ASCII UTF-8 comment and `"??x"` literal | Accepted | `safe text` retains each boundary in C, headers and patches; `live metadata` keeps an indented addition |
| Full `diff --git`, `index` with and without mode, `new file mode`, `--- /dev/null`, `+++ b/` | Accepted | `live metadata` asserts exact text and added line 8; `metadata between hunks` keeps file headers outside lexical input |
| Completed hunk, adjacent no-newline marker, then a second hunk containing a mapped read | Read refused at its added line; no unreadable error | `completed marker then read` asserts exact masked text and origins `{2, 5}`; the full checker integration asserts the second-hunk read at `c.patch:9` and no extra error |
| Digraph subscript or alias read; UCN or UTF-8 alias; `#define` or `%:define` mapped read | Refused | The existing direct-string classifier cases remain refused; real files reject digraphs before masking and reject `mapped_mem` in spliced directives independently of statement classification |
| `vf_pelorus_analyze_vulkan.c:710` | Known, #596 | `base` feeds per-tile reads; cost scales with resolution |
| `vf_pelorus_denoise_vulkan.c:814` | Known, #596 | `acc_out` exposes a fixed 160-word accumulator |
| `vf_pelorus_grain_estimate_vulkan.c:907` | Known, #596 | `attach_estimate()` reads a fixed statistics struct; collapsed statement includes its preceding `if (err >= 0)` |

The scanner is lexical, not a C parser, and models translation phases 1–2 without macro expansion (phase 4). Macros from other files are outside the scan. Unterminated literals and block comments remain unmasked; C23 `1'000` is treated as an unterminated character literal. Comments retain their width and newlines; splices defer swallowed newlines to the next surviving newline or EOF. The scanner does not follow pointers obtained through `vk->MapMemory` or wrapper implementations. `--files DIR` redirects both source checks to a copy for mutation verification. An input tree with no `mapped_mem` token fails closed.

## Verification

`patch_regions()` reads each hunk by its declared old/new counts and produces an independent masked text, added-origin set and blank prefix offset for diagnostics. Omitted counts default to one. Space-prefixed and empty body lines consume both counts, removals consume only the old count, additions consume only the new count, and backslash markers consume neither. Header-shaped lines inside a body remain ordinary body lines. A body ends when both counts reach zero. Unknown body prefixes, negative counts, incomplete bodies, malformed outside-body `@@` headers and patches with no hunk fail closed. `source_texts()` retains a `PatchError` for the unreadable patch; `check()` emits its single `file:line: unreadable patch hunk ...` diagnostic, and the CLI prints it and exits 1. Only actual new-side context and additions enter lexing, joined directly in order; removals and markers are restored as blank output positions afterward. Splice origins follow the previous new-side line rather than the previous patch line. Outside-body lines must fully match the `c_source.py` allow-list: paired `diff --git` paths; hexadecimal `index` with optional regular-file mode `100644` or `100755`; `new file mode` with either regular-file mode; old `a/` or `/dev/null` header; new `b/` header; counted hunk header; or a backslash-space marker immediately following a completed body. Binary, rename, mail and other unknown shapes fail closed, as do blank lines and trailing additions. A `GIT binary patch` section can write content, so outside-body text cannot be ignored wholesale. The mode allow-list mirrors the live tree: `120000` writes a symlink and `160000` a gitlink, neither of which is C source the lexer can classify. Strict UTF-8 decoding and LF-only splitting precede parsing and masking; control characters other than tab, Unicode format characters (category Cf, including a byte-order mark) and trigraphs fail closed in raw text. The shared `preprocessing_hazard()` checks raw and spliced new-side text for digraphs and `##` before masking, retaining the physical origin of a joined hazard. `scan_directives()` consumes the existing spliced and masked text, reports `mapped_mem` or a declared or assigned mapped alias at the first directive line without requiring added-line overlap, including context directives because a context macro can carry an added read, and blanks directive text without moving newlines. The blanked text feeds mapped-read classification only; the allocation rule reads directive text, including macro bodies. Source loading and patch parsing live in `c_source.py`; lexing lives in `c_scan.py`. Allocation fixtures and their flag constants live in the data-only module `vulkan_buffer_cases.py`. No statement, call, argument, alias tail or flag lookup crosses a region. `source_texts()` supplies those regions independently; `patch_source()` combines them only for lexical fixtures. The shared `argument_spans()` scanner tracks parentheses, brackets and braces; `call_args()` and `argument_index()` derive their results from it. A comma separates arguments only at depth one, and only a closing parenthesis returning depth to zero ends the call. The pointer-expression helper counts mapped tokens before stripping casts. Only `;`, `{`, `}` outside nested argument spans end a statement. Statement starts use `argument_spans()` to retain a callee before a compound literal; an unmatched `)` cannot advance the boundary. Refused mapped statements require overlap from statement start through statement end, extended through the scanned alias tail; a missing closing brace fails closed through the available region end. Allocation calls require overlap through the closing parenthesis, or through the available bounded call scan when unreadable. Exact scanner tests assert masked text, line count and added origins.

Run the focused checker from the repository root:

```bash
python3 -I scripts/check-vulkan-buffer-domain.py --self-test
python3 -I scripts/check-vulkan-buffer-domain.py
ruff check --no-cache --config ruff.toml scripts/check-vulkan-buffer-domain.py scripts/c_scan.py scripts/c_source.py
ruff format --no-cache --check --config ruff.toml scripts/check-vulkan-buffer-domain.py scripts/c_scan.py scripts/c_source.py
python3 -I scripts/test-semgrep-strict-reader-fixture.py
semgrep scan --config .semgrep.yml --error --metrics=off --disable-version-check
```

The live tree and self-tests exit 0. The inventory of 34 scanned files has only LF, no control or format characters or tabs, no `??` or digraph-shaped sequences, and 58 lines with non-ASCII UTF-8 in comments. The strict reader accepts the live tree without exceptions. File integration uses `TemporaryDirectory` with `a.c` (valid storage allocation and copy-out), `b.h` (cast read), `c.patch` (ignored context read, added read at line 5, host-only storage allocation at line 6, second-hunk added read at line 9 after a no-newline marker), and the three unchanged known-read fixtures. It requires exactly those four new diagnostics and no empty-scan failures. The additional patch fixtures pass through `source_texts()` and full `check()` calls using real patch files plus valid allocation and known-read baselines. They assert mapped-use and allocation diagnostics separately, including unreadable calls, unresolvable flags and exactly one error for each malformed patch. The hand-maintained patch, increment, reader and comment-close fixtures run through this integration path with count-consistent headers. Self-tests cover bulk copies and stores, unsafe calls and aliases, patch origin boundaries, exact exception coverage, unknown categories, an empty scan, expiry at day 121 and acceptance at day 120. Standalone mapped-case diagnostics are also checked for doubled semicolons. The pinned Ruff (`ruff==0.17.3` in `.config/python-lint/requirements.txt`) reports clean lint and formatting.

### Git apply boundaries

Measured with Git 2.56.0 in scratch directories using plain `git apply p.diff`, with file headers `--- a/x.c` and `+++ b/x.c`. The applied patch path is used by `ffmpeg-patches/generate.sh` via `git -C "$WORKTREE" apply`.

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

The following measurements used baseline `56ba1af`, Git 2.56.0, GCC 16.2.1 and clang 23.1.4 with `-std=c17 -E -P`. Both compilers produced the stated result. The CR, CR-splice and binary-patch rows above used the same baseline and Git version. The pinned FFmpeg `n9.0.2` (`build-config.env`) configure line 4412 sets `stdc_default="c17"`; C23's removal of trigraphs therefore does not remove this input hazard.

| Input | Measured compiler result | Checker on `56ba1af` / current reader |
| --- | --- | --- |
| `int x = b->mapped_??/` LF `mem[0];` | `int x = b->mapped_mem[0];` | Accepted / unreadable trigraph |
| `char c = '??/''; int x = b->mapped_mem[0];` | Character literal becomes `'\''`; mapped read remains code | Accepted / unreadable trigraph |
| `mapped` NUL `_mem` | Two tokens, with no mapped read | No read / unreadable control character |

Trigraph translation occurs before comment and literal recognition. Lone CR is a C line boundary even though patch lines split only at LF. Digraph subscripts and alias reads, UCN/UTF-8 aliases and both `#define` spellings were refused on the measured baseline: the mapped token remained visible. The checker rejects digraphs and token pastes in both raw and spliced new-side text before masking. Directives naming `mapped_mem` or mapped aliases fail closed. UCNs remain untranslated; macro expansion (phase 4) and macros from other files remain outside the scan. The two later digraph bypasses below demonstrate why retaining a visible token alone was insufficient. CRLF sources fail closed.

### Self-test strength

The boundary coverage comprises 161 cases: 88 splice cases, 45 alias cases, nine call-boundary cases, twelve assignment/format cases, two direct alias-tail contract cases and five directive allocation/classification cases.

The splice cases cover all five digraphs, token paste, comments, literals and safe continuations.

Physical origins remain exact after multiple splices and removed patch lines.

Alias cases cover both macro forms, declaration order, context directives, removed declarations, adjacent replacement tokens and UCN macro names. Both four-digit and eight-digit UCN function-like names accept a formal parameter named like the mapped alias.

Five call-boundary cases inspect statement offsets, including a call longer than 4,096 characters and an argument tail whose opening parenthesis precedes the hunk. Each also checks that the delimiter boundary is no later than the flat boundary, before leading-whitespace trimming preserves added-line overlap.

Four call-boundary cases also run complete patches through the checker.

The shared `mapped_binding()` strips casts and grouping parentheses, stops at the nearest semicolon, brace or remaining call parenthesis, and identifies the nearest assigned name. An opening parenthesis is grouping when the previous non-blank character is not a name character, `)` or `]`; otherwise it opens a call. `binding_tail()` extends refused bindings on both classifier paths. A for-loop initializer conservatively reaches the enclosing block’s end; additions in another block after that enclosing brace remain outside the region. Missing closing braces extend through available text. `mapped_aliases()` uses overlapping assignment matches for directive checks, including chained assignments, while excluding equality comparisons.

| Case | Input | Expected diagnostic or acceptance |
| --- | --- | --- |
| `assigned directive` | Context declaration and cast assignment, added macro using the alias | Patch line 8 directive refusal and line 7 mapped-read refusal |
| `chained context directive` | Context declaration, chained assignment `q = p = ...` and a macro naming the second name, added macro use | Patch line 8 directive refusal and line 7 mapped-read refusal |
| `assignment read` | Context cast assignment, added alias read | Patch line 7 mapped-read refusal |
| `const read` | Context const declaration, added alias read | Patch line 6 mapped-read refusal |
| `for-init read` | Context for-loop initializer, added body read | Patch line 6 mapped-read refusal |
| `assignment outside block` | Added unrelated block after the alias block closes | Accepted |
| `for-init after loop` | Added block after the loop closes, within its enclosing block | Patch line 7 mapped-read refusal |
| `for-init outside block` | Added unrelated block after the enclosing block closes | Accepted |
| `format added` | U+200B inside an added patch line | Patch line 6 unreadable format character |
| `format context` | U+200B inside a context comment | Patch line 6 unreadable format character; whole patch refused |
| `BOM source` | U+FEFF before a first-line alias macro in C | Source line 1 unreadable format character |
| `BOM patch` | Same C source as a counted new-file patch | Patch line 7 unreadable format character |
| Closed alias tail | Plain alias store followed by a closing brace | `store_only_tail()` returns `(True, 10)` |
| Open alias tail | Same store without a closing brace | `store_only_tail()` returns `(False, 9)` |
| `patch macro allocation` | Added host-only storage allocation in a macro body | Storage-buffer refusal at patch line 2 |
| `patch device-local macro allocation` | Added device-local storage allocation in a macro body | Accepted |
| `patch context macro allocation` | Context host-only macro followed by an unrelated addition | Accepted |
| `macro.c` in `source_self_test()` | Host-only storage allocation in a macro body after a comment | Exactly one storage-buffer refusal at `macro.c:2` |
| `mapped.c` in `source_self_test()` | Macro body naming `mapped_mem` | Exactly one directive refusal at `mapped.c:1`; no element-wise diagnostic |

The assignment/format fixtures use byte writes and check diagnostic counts and line numbers through `source_texts()` and the complete checker with valid allocation and known-read baselines. Disabling assignment alias collection, binding-tail extension and format-character refusal together in a copy produces exit 1: all ten refused assignment/format fixtures become accepted. The two unrelated-block controls remain accepted. Direct tail contracts independently detect removal of the closing-brace and available-region endpoints.

All 120 non-equivalent checker rule mutants below exited 1 on 2026-11-11. Copy all four Python files into `$TMPDIR/<name>/scripts/`, make one exact replacement, and run `python3 -I <copy>/check-vulkan-buffer-domain.py --self-test --files ffmpeg-patches/files` from the repository root. Each replacement is represented by exact Python string literals, including indentation and escaped newlines; require exactly one occurrence of `old` across all four files before `text.replace(old, new, 1)` in the named file. The table also records four equivalent mutants and two harness removals with expected exit 0.

All 127 checker table rows were rerun against the files: 120 exited 1 and seven retained their expected exit 0. Combining `start-invariant-off` with `close-paren-boundary` also exits 1: the complete argument-tail patch still detects the regression. Parser and reader rows name `c_source.py`. The twenty-three Semgrep and importer-test mutants below also exited 1 with Semgrep 1.183.0.

Reader and metadata plants fail when their corresponding checks are disabled. The `metadata between hunks` fixture detects a body scan that includes a following file header. Header-shaped reads, subscript arguments, nested pointers, patch context, open aliases, leading whitespace, excluded-line splices and cross-hunk boundaries each have independent refusal cases. The mode plants fail against the six-digit octal allow-list before the regular-file restriction is applied.

| Mutant | File | Exact replacement (`old` → `new`) | Observed exit | First failing self-test case |
| --- | --- | --- | --- | --- |
| assignment-alias-off | `c_scan.py` | `'r"(?=\\b(\\w+)\\s*=(?!=)[^;{}]*\\bmapped_mem\\b)"'` → `'r"\\*\\s*(\\w+)\\s*=\\s*[^;{}]*\\bmapped_mem\\b"'` | 1 | `lexical case failed: assigned directive: ['x.patch:7: element-wise host read of mapped device memory: p = (uint8_t *)b->mapped_mem; copy it out with one memcpy and read the copy (ADR-0255)']` |
| chained-alias-off | `c_scan.py` | `'r"(?=\\b(\\w+)\\s*=(?!=)[^;{}]*\\bmapped_mem\\b)"'` → `'r"\\b(\\w+)\\s*=(?!=)[^;{}]*\\bmapped_mem\\b"'` | 1 | `lexical case failed: chained context directive: ['x.patch:7: element-wise host read of mapped device memory: q = p = (uint8_t *)b->mapped_mem; copy it out with one memcpy and read the copy (ADR-0255)']` |
| const-tail-off | `c_scan.py` | `'    if mapped_binding(text, pos) is None:\n        return end'` → `'    if mapped_binding(text, pos) is None or re.search(\n        r"\\bconst\\b", text[statement_bounds(text, pos)[0]:pos]\n    ):\n        return end'` | 1 | `lexical case failed: const read: []` |
| assignment-tail-off | `c_scan.py` | `'    if mapped_binding(text, pos) is None:\n        return end'` → `'    if mapped_binding(text, pos) is None or re.match(\n        r"\\w+\\s*=", text[statement_bounds(text, pos)[0]:pos]\n    ):\n        return end'` | 1 | `lexical case failed: assigned directive: ['x.patch:8: mapped_mem in preprocessor directive']` |
| call-binding-tail-off | `check-vulkan-buffer-domain.py` | `'text, start, binding_tail(text, pos, end), added'` → `'text, start, (end if call else binding_tail(text, pos, end)), added'` | 1 | `lexical case failed: for-init read: []` |
| binding-name-off | `c_scan.py` | `'return binding[1] if binding else None'` → `'return None'` | 1 | `patch case failed: patch grouped context alias: False, []` |
| grouping-strip-off | `c_scan.py` | `'    grouped = re.sub(r"(?<=[^\\w)\\]\\s])\\s*\\(", " ", re.sub(CAST, "", text[:pos]))'` → `'    grouped = re.sub(CAST, "", text[:pos])'` | 1 | `patch case failed: patch grouped context alias: False, []` |
| grouping-strip-calls | `c_scan.py` | `'r"(?<=[^\\w)\\]\\s])\\s*\\("'` → `'r"\\s*\\("'` | 1 | `patch case failed: patch context reader: True, []` |
| grouping-strip-close | `c_scan.py` | `'r"(?<=[^\\w)\\]\\s])\\s*\\("'` → `'r"(?<=[^\\w\\]\\s])\\s*\\("'` | 1 | `patch case failed: patch context reader: True, []` |
| grouping-strip-bracket | `c_scan.py` | `'r"(?<=[^\\w)\\]\\s])\\s*\\("'` → `'r"(?<=[^\\w)\\s])\\s*\\("'` | 1 | `patch case failed: patch context reader: True, []` |
| grouping-strip-blank | `c_scan.py` | `'r"(?<=[^\\w)\\]\\s])\\s*\\("'` → `'r"(?<=[^\\w)\\]])\\s*\\("'` | 1 | `patch case failed: patch context reader: True, []` |
| format-category-off | `c_source.py` | `'hazards = categories.intersection(("Cc", "Cf"))'` → `'hazards = categories.intersection(("Cc",))'` | 1 | `lexical case failed: format added: []` |
| patch-format-off | `c_source.py` | `'hazards = categories.intersection(("Cc", "Cf"))'` → `'hazards = categories.intersection(("Cc", "Cf") if kind == "source" else ("Cc",))'` | 1 | `lexical case failed: format added: []` |
| classifier-off | `check-vulkan-buffer-domain.py` | `'if not allowed and added_overlap(\n            text, start, binding_tail(text, pos, end), added\n        ):'` → `'if False:'` | 1 | `mapped case failed: digraph subscript: []` |
| store-lookahead | `check-vulkan-buffer-domain.py` | `'=(?!=)"'` → `'="'` | 1 | `mapped case failed: write alias equality: []` |
| single-mapped | `check-vulkan-buffer-domain.py` | `'if len(re.findall(r"\\bmapped_mem\\b", statement)) != 1:'` → `'if False:'` | 1 | `mapped case failed: store with mapped RHS: []` |
| const-rule | `check-vulkan-buffer-domain.py` | `'if not alias or re.search(r"\\bconst\\b", alias[1] + " " + alias[3]):'` → `'if not alias:'` | 1 | `mapped case failed: const alias store: []` |
| pointer-expression | `c_scan.py` | `'return re.fullmatch(chain, plain) is not None'` → `'return True'` | 1 | `mapped case failed: offset: []` |
| parenthesis-merge | `c_scan.py` | `'    if re.search(r"[\\w)\\]]\\s*\\(", plain):\n        return False\n'` → `''` | 1 | `mapped case failed: parenthesised reader: []` |
| expiry | `check-vulkan-buffer-domain.py` | `'today > expiry'` → `'today >= expiry'` | 1 | `known-read case failed: boundary: ['known read vf_pelorus_analyze_vulkan.c expired on 2027-03-11 (issue #596, MEL category D)']` |
| sink-index | `check-vulkan-buffer-domain.py` | `'index == SINKS.get(name, (-1, ""))[0]'` → `'name in SINKS'` | 1 | `mapped case failed: subscript sink input: []` |
| mapped-fail-closed | `check-vulkan-buffer-domain.py` | `'if mapped_total == 0:'` → `'if False:'` | 1 | `empty tree accepted: no mapped_mem use found` |
| duplicate-sites | `check-vulkan-buffer-domain.py` | `'if count != 1:'` → `'if count == 0:'` | 1 | `known-read case failed: duplicate: []` |
| alias-uses | `c_scan.py` | `'    allowed = all(\n        re.match(store, tail[use.end() :]) and statement_start(tail, use.start())\n        for use in uses\n    )'` → `'    allowed = True'` | 1 | `mapped case failed: digraph alias: []` |
| semicolon | `check-vulkan-buffer-domain.py` | `"{statement.removesuffix(';')}"` → `'{statement}'` | 1 | `doubled semicolon: digraph subscript: ['mapped.c:1: element-wise host read of mapped device memory: x = b->mapped_mem<:0:>;; copy it out with one memcpy and read the copy (ADR-0255)']` |
| mask-identity | `c_scan.py` | `'    text = _splice(text)'` → `'    return text\n    text = _splice(text)'` | 1 | `mapped case failed: quoted brace alias: []` |
| literals-off | `c_scan.py` | `'elif text[pos] in "\\"\'":'` → `'elif False:'` | 1 | `mapped case failed: quoted brace alias: []` |
| splices-off | `c_scan.py` | `'    text = _splice(text)'` → `'    text = text'` | 1 | `mapped case failed: spliced token: []` |
| unterminated-unmask-off | `c_scan.py` | `'return text[start:pos], pos'` → `'return text[start] + " " * (pos - start - 1), pos'` | 1 | `C masking case failed: unterminated character: "x = '                         \nx;"` |
| patch-context-off | `c_scan.py` | `'line.startswith(("+", " "))'` → `'line.startswith("+")'` | 1 | `C statement case failed: (struct Host){ .p = host }.p` |
| member-guard-off | `c_scan.py` | `'("" if member else name[1])'` → `'name[1]'` | 1 | `mapped case failed: member dot source: []` |
| bulk-statement-off | `check-vulkan-buffer-domain.py` | `'if name in BULK and index == 0:'` → `'if False:'` | 1 | `mapped case failed: memset result reader: []` |
| type-allowlist-off | `check-vulkan-buffer-domain.py` | `'({TYPE}(?:\\s+{TYPE})*)'` → `'(\\w+(?:\\s+\\w+)*)'` | 1 | `mapped case failed: return output: []` |
| alias-statement-off | `c_scan.py` | `' and statement_start(tail, use.start())'` → `''` | 1 | `mapped case failed: chained alias store: []` |
| duplicate-entry-off | `check-vulkan-buffer-domain.py` | `'if key in seen:'` → `'if False:'` | 1 | `known-read case failed: duplicate entries: []` |
| bulk-suffix-off | `check-vulkan-buffer-domain.py` | `'return prefix in ("", "(void)") and suffix == ";"'` → `'return prefix in ("", "(void)")'` | 1 | `mapped case failed: memcpy result suffix: []` |
| statement-close-paren-off | `c_scan.py` | `'";{})"'` → `'";{}"'` | 1 | `mapped case failed: write alias: ['mapped.c:1: element-wise host read of mapped device memory: uint32_t *md = (uint32_t *)b->mapped_mem; copy it out with one memcpy and read the copy (ADR-0255)']` |
| statement-close-brace-off | `c_scan.py` | `'";{})"'` → `'";{)"'` | 1 | `mapped case failed: write alias: ['mapped.c:1: element-wise host read of mapped device memory: uint32_t *md = (uint32_t *)b->mapped_mem; copy it out with one memcpy and read the copy (ADR-0255)']` |
| statement-else-do-off | `c_scan.py` | `'bool(re.search(r"\\b(?:else\|do)$", prefix))'` → `'False'` | 1 | `mapped case failed: write alias: ['mapped.c:1: element-wise host read of mapped device memory: uint32_t *md = (uint32_t *)b->mapped_mem; copy it out with one memcpy and read the copy (ADR-0255)']` |
| type-uppercase-off | `check-vulkan-buffer-domain.py` | `'\|[A-Z]\\w*'` → `''` | 1 | `mapped case failed: alias first store: ['mapped.c:1: element-wise host read of mapped device memory: T *p = (T *)b->mapped_mem; copy it out with one memcpy and read the copy (ADR-0255)']` |
| type-tag-off | `check-vulkan-buffer-domain.py` | `'\|(?:struct\|union\|enum)\\s+\\w+'` → `''` | 1 | `mapped case failed: alias type groups: ['mapped.c:1: element-wise host read of mapped device memory: struct s *c = (struct s *)b->mapped_mem; copy it out with one memcpy and read the copy (ADR-0255)']` |
| type-scalars-off | `check-vulkan-buffer-domain.py` | `'const\|volatile\|restrict\|signed\|unsigned\|short\|long\|int\|char\|float\|double\|void\|_Bool'` → `'const'` | 1 | `mapped case failed: alias type groups: ['mapped.c:1: element-wise host read of mapped device memory: int *d = (int *)b->mapped_mem; copy it out with one memcpy and read the copy (ADR-0255)']` |
| block-nesting-off | `c_scan.py` | `'if text[pos] == "{":'` → `'if False:'` | 1 | `mapped case failed: nested block alias escape: []` |
| literal-newline-off | `c_scan.py` | `'        if char == "\\n":\n            return text[start:pos], pos\n'` → `''` | 1 | `C masking case failed: literal newline stop: "x = '                              'b';"` |
| hunk-reset-off | `c_source.py` | `'masked, added = patch_segment(lines[start : i + 1])'` → `'masked, added = patch_segment(lines[: i + 1])\n        masked, added = masked[start:], {n - start for n in added if n > start}'` | 1 | `C patch case failed: segment reset: '\n/*\n\n                      \n  '` |
| statement-empty-prefix-off | `c_scan.py` | `'not prefix or prefix[-1]'` → `'prefix and prefix[-1]'` | 1 | `alias tail case failed: p[0] = 1; }` |
| statement-at-close-brace-off | `c_scan.py` | `'r"[();{}]"'` → `'r"[();{]"'` | 1 | `mapped case failed: store after block: ['mapped.c:1: element-wise host read of mapped device memory: } ((uint32_t *)b->mapped_mem)[0] = 0; copy it out with one memcpy and read the copy (ADR-0255)']` |
| new-header-previous-off | `c_scan.py` | `'if not line or line.startswith(("+", " "))'` → `'if not line or (line.startswith(("+", " ")) and not line.startswith("+++ "))'` | 1 | `C patch case failed: added pre-increment: '\n'` |
| call-bracket-depth-off | `c_scan.py` | `'if char in "([{":\n            depth += 1\n        elif char in ")]}":'` → `'if char in "({":\n            depth += 1\n        elif char in ")}":'` | 1 | `mapped case failed: subscript sink input: []` |
| call-brace-depth-off | `c_scan.py` | `'if char in "([{":\n            depth += 1\n        elif char in ")]}":'` → `'if char in "([":\n            depth += 1\n        elif char in ")]":'` | 1 | `C argument case failed: (S){a, b, c}.p: (['(S){a', 'b', 'c}.p', 'mapped_mem', 'n'], 31), 3` |
| pointer-single-token-off | `c_scan.py` | `'if len(re.findall(rf"\\b{re.escape(token)}\\b", arg)) != 1:'` → `'if False:'` | 1 | `mapped case failed: nested mapped source: []` |
| patch-additions-only | `c_scan.py` | `'return out, added'` → `'return [line if i + 1 in added else "" for i, line in enumerate(out)], added'` | 1 | `C statement case failed: (struct Host){ .p = host }.p` |
| splice-origin-off | `c_scan.py` | `'added.add(origin + 1)'` → `'added.add(i + 1)'` | 1 | `C statement case failed: (struct Host){ .p = host }.p` |
| alias-region-tail-off | `c_scan.py` | `'return allowed, start + len(tail)'` → `'return allowed, start'` | 1 | `alias tail case failed: p[0] = 1; }` |
| mapped-origin-off | `check-vulkan-buffer-domain.py` | `'if not allowed and added_overlap(\n            text, start, binding_tail(text, pos, end), added\n        ):'` → `'if not allowed:'` | 1 | `source tree case failed: ['c.patch:6: storage buffer without VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT (memory flags: VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT \| VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)', 'macro.c:2: storage buffer without VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT (memory flags: VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT \| VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)', 'mapped.c:1: mapped_mem in preprocessor directive', 'b.h:1: element-wise host read of mapped device memory: x = ((T *)b->mapped_mem)[0]; copy it out with one memcpy and read the copy (ADR-0255)', 'c.patch:4: element-wise host read of mapped device memory: reader(b->mapped_mem); copy it out with one memcpy and read the copy (ADR-0255)', 'c.patch:5: element-wise host read of mapped device memory: reader(b->mapped_mem); copy it out with one memcpy and read the copy (ADR-0255)', 'c.patch:9: element-wise host read of mapped device memory: reader(b->mapped_mem); copy it out with one memcpy and read the copy (ADR-0255)']` |
| allocation-additions-only | `check-vulkan-buffer-domain.py` | `'    """Return (errors, number of calls read) for one source text."""'` → `'    """Return (errors, number of calls read) for one source text."""\n    text = "\\n".join(line if added is None or i + 1 in added else "" for i, line in enumerate(text.split("\\n")))'` | 1 | `patch case failed: patch split allocation: False, []` |
| allocation-origin-off | `check-vulkan-buffer-domain.py` | `'if not added_overlap(text, match.start(), end, added):'` → `'if False:'` | 1 | `patch case failed: patch context macro allocation: False, ['test.patch:2: storage buffer without VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT (memory flags: VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT \| VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)']` |
| statement-leading-space | `c_scan.py` | `'    start += len(text[start:pos]) - len(text[start:pos].lstrip())\n'` → `''` | 1 | `C statement case failed: (struct Host){ .p = host }.p` |
| alias-open-region-off | `c_scan.py` | `'return False, len(text)'` → `'return False, start'` | 1 | `alias tail case failed: p[0] = 1;` |
| excluded-lines-lexed | `c_scan.py` | `'    positions = [\n        i for i, line in enumerate(lines) if not line or line.startswith(("+", " "))\n    ]\n    side = [lines[i][1:] for i in positions]'` → `'    positions = list(range(len(lines)))\n    side = [line[1:] if line.startswith(("+", " ")) else "" for line in lines]'` | 1 | `C patch case failed: removed decrement splice: '\nx = b->mapped_\n\nmem;'` |
| splice-origin-patch-order | `c_scan.py` | `'side[i - 1]'` → `'lines[pos - 1][1:]'` | 1 | `C patch case failed: removed decrement splice: '\nx = b->mapped_mem;\n\n'` |
| region-barrier-off | `c_source.py` | `'patch_regions(raw)'` → `'[patch_source(raw)]'` | 1 | `patch case failed: patch hunk call: False, []` |
| alias-region-end-accepted | `c_scan.py` | `'return False, len(text)'` → `'return True, len(text)'` | 1 | `alias tail case failed: p[0] = 1;` |
| flag-resolution-across-regions | `c_source.py` | `'"\\n" * start + "\\n".join(masked)'` → `'((regions[-1][0] + "\\n" * (start - regions[-1][0].count("\\n"))) if regions else "\\n" * start) + "\\n".join(masked)'` | 1 | `C directive patch case failed: '@@ -0,0 +1 @@\n+#define P p\n@@ -8 +9 @@\n { T *p = (T *)b->mapped_mem;': ['2: mapped_mem in preprocessor directive', '2: mapped_mem in preprocessor directive']` |
| close-paren-only-off | `c_scan.py` | `'if depth == 0 and char == ")":'` → `'if depth == 0:'` | 1 | `mapped case failed: bracket call closer: []` |
| missing-count-zero | `c_source.py` | `'else 1 for i in (2, 4)'` → `'else 0 for i in (2, 4)'` | 1 | `C patch case failed: metadata between hunks: 2: unreadable patch metadata` |
| context-new-only | `c_source.py` | `'old, new = old - 1, new - 1'` → `'old, new = old, new - 1'` | 1 | `C patch case failed: empty context: 4: unreadable patch hunk at EOF` |
| removed-new-count | `c_source.py` | `'old -= 1'` → `'new -= 1'` | 1 | `C patch case failed: removed decrement splice: 4: unreadable patch hunk negative count` |
| added-old-count | `c_source.py` | `'new -= 1'` → `'old -= 1'` | 1 | `C patch case failed: metadata between hunks: 2: unreadable patch hunk negative count` |
| empty-context-off | `c_source.py` | `'if prefix in ("", " "):'` → `'if prefix == " ":'` | 1 | `C patch case failed: empty context: 2: unreadable patch hunk body prefix` |
| open-hunk-accepted | `c_source.py` | `'    if old or new:\n        raise PatchError(f"{len(lines) + 1}: unreadable patch hunk at EOF")'` → `'    if False:\n        raise PatchError(f"{len(lines) + 1}: unreadable patch hunk at EOF")'` | 1 | `C source case failed: incomplete body: []` |
| negative-count-accepted | `c_source.py` | `'if min(old, new) < 0:'` → `'if False:'` | 1 | `C source case failed: negative old count: ['4: unreadable patch hunk at EOF']` |
| unknown-prefix-ends | `c_source.py` | `'raise PatchError(f"{number}: unreadable patch hunk body prefix")'` → `'return 0, 0'` | 1 | `C source case failed: unknown prefix: []` |
| malformed-header-metadata | `c_source.py` | `'raise PatchError(f"{number}: unreadable patch hunk header")'` → `'return None'` | 0 | `None; expected 0` |
| no-hunk-accepted | `c_source.py` | `'if not seen:'` → `'if False:'` | 1 | `C source case failed: no hunk: []` |
| header-shape-readded | `c_source.py` | `'        if old or new:\n            old, new = _consume_line(line, old, new, i + 1)'` → `'        if old or new:\n            if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):\n                old = new = 0\n                continue\n            old, new = _consume_line(line, old, new, i + 1)'` | 1 | `patch case failed: patch header-shaped read: False, ['test.patch:5: unreadable patch metadata']` |
| body-past-counts | `c_source.py` | `'masked, added = patch_segment(lines[start : i + 1])'` → `'masked, added = patch_segment(lines[start : next((j for j in range(i + 1, len(lines)) if lines[j].startswith("@@")), len(lines))])'` | 1 | `C patch case failed: metadata between hunks: '\nx;\n\n++ b/y.c\n\ny;'` |
| patch-error-drop | `check-vulkan-buffer-domain.py` | `'errors.append(f"{name}:{text}")'` → `'pass'` | 1 | `source tree case failed: ['c.patch:6: storage buffer without VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT (memory flags: VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT \| VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)', 'macro.c:2: storage buffer without VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT (memory flags: VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT \| VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)', 'b.h:1: element-wise host read of mapped device memory: x = ((T *)b->mapped_mem)[0]; copy it out with one memcpy and read the copy (ADR-0255)', 'c.patch:5: element-wise host read of mapped device memory: reader(b->mapped_mem); copy it out with one memcpy and read the copy (ADR-0255)', 'c.patch:9: element-wise host read of mapped device memory: reader(b->mapped_mem); copy it out with one memcpy and read the copy (ADR-0255)']` |
| universal-newlines | `c_source.py` | `'raw = data.decode("utf-8")'` → `'raw = path.read_text(encoding="utf-8")'` | 1 | `C source case failed: CRLF patch: []` |
| control-check-off | `c_source.py` | `'if hazards:'` → `'if False:'` | 1 | `C source case failed: CRLF patch: []` |
| CR-allowed | `c_source.py` | `'c not in "\\t"'` → `'c not in "\\t\\r"'` | 1 | `C source case failed: CRLF patch: []` |
| NUL-allowed | `c_source.py` | `'c not in "\\t"'` → `'c not in "\\t\\0"'` | 1 | `C source case failed: '\x00': []` |
| decode-replace | `c_source.py` | `'data.decode("utf-8")'` → `'data.decode("utf-8", errors="replace")'` | 1 | `C source case failed: invalid UTF-8: []` |
| decode-skip | `c_source.py` | `'texts.append((path.name, error, None))'` → `'if "UTF-8" not in str(error):\n                    texts.append((path.name, error, None))'` | 1 | `C source case failed: invalid UTF-8: []` |
| trigraph-off | `c_source.py` | `'if re.search(r"\\?\\?[=(/)\'<!>-]", line):'` → `'if False:'` | 1 | `C source case failed: x = b->mapped_??/` |
| trigraph-slash-only | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?/"'` | 1 | `C source case failed: /* ??( */: []` |
| trigraph-without-equals | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?[(/)\'<!>-]"'` | 1 | `C source case failed: // ??=: []` |
| trigraph-without-left-paren | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?[=/)\'<!>-]"'` | 1 | `C source case failed: /* ??( */: []` |
| trigraph-without-slash | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?[=()\'<!>-]"'` | 1 | `C source case failed: x = b->mapped_??/` |
| trigraph-without-right-paren | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?[=(/\'<!>-]"'` | 1 | `C source case failed: // ??): []` |
| trigraph-without-quote | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?[=(/)<!>-]"'` | 1 | `C source case failed: // ??': []` |
| trigraph-without-less | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?[=(/)\'!>-]"'` | 1 | `C source case failed: // ??<: []` |
| trigraph-without-bang | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?[=(/)\'<>-]"'` | 1 | `C source case failed: // ??!: []` |
| trigraph-without-greater | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?[=(/)\'<!-]"'` | 1 | `C source case failed: // ??>: []` |
| trigraph-without-minus | `c_source.py` | `'r"\\?\\?[=(/)\'<!>-]"'` → `'r"\\?\\?[=(/)\'<!>]"'` | 1 | `C source case failed: // ??-: []` |
| metadata-off | `c_source.py` | `'elif not _patch_metadata(line, i == marker):'` → `'elif False:'` | 1 | `C source case failed: trailing addition: []` |
| metadata-prefix | `c_source.py` | `'re.fullmatch(shapes, line)'` → `'re.match(shapes, line)'` | 1 | `C source case failed: diff --git a/x.c b/x.c junk: []` |
| marker-anywhere | `c_source.py` | `'(marker and line.startswith("\\\\ "))'` → `'line.startswith("\\\\ ")'` | 1 | `C source case failed: marker before hunk: []` |
| new-header-dev-null | `c_source.py` | `'\\+\\+\\+ b/\\S+'` → `'\\+\\+\\+ (?:b/\\S+\|/dev/null)'` | 1 | `C source case failed: +++ /dev/null: []` |
| index-any-text | `c_source.py` | `'index [0-9a-f]+\\.\\.[0-9a-f]+(?: 100(?:644\|755))?'` → `'index .*'` | 1 | `C source case failed: index zz..yy: []` |
| new-mode-any | `c_source.py` | `'\|new file mode 100(?:644\|755)'` → `'\|new file mode .*'` | 1 | `C source case failed: new file mode 120000: []` |
| new-mode-octal | `c_source.py` | `'\|new file mode 100(?:644\|755)'` → `'\|new file mode [0-7]{6}'` | 1 | `C source case failed: new file mode 120000: []` |
| index-mode-octal | `c_source.py` | `'index [0-9a-f]+\\.\\.[0-9a-f]+(?: 100(?:644\|755))?'` → `'index [0-9a-f]+\\.\\.[0-9a-f]+(?: [0-7]{6})?'` | 1 | `C source case failed: index ab..cd 120000: []` |
| patch-segment-CR-optional | `c_scan.py` | `'r"\\\\[ \\t]*$"'` → `'r"\\\\[ \\t]*\\r?$"'` | 0 | `None; expected 0` |
| source-harness-off | `check-vulkan-buffer-domain.py` | `' + reading_self_test()'` → `''` | 0 | `None; expected 0` |
| scan-harness-off | `check-vulkan-buffer-domain.py` | `' + scan_self_test()'` → `''` | 0 | `None; expected 0` |
| keyword-skip | `c_scan.py` | `'if name:'` → `'if name and name[1] not in {"if", "while", "for", "switch", "return", "sizeof"}:'` | 0 | `None; expected 0` |
| digraph-without-left-bracket | `c_scan.py` | `'r"<:\|:>\|<%\|%>\|%:"'` → `'r":>\|<%\|%>\|%:"'` | 1 | `C source case failed: <:: []` |
| digraph-without-right-bracket | `c_scan.py` | `'r"<:\|:>\|<%\|%>\|%:"'` → `'r"<:\|<%\|%>\|%:"'` | 1 | `C source case failed: :>: []` |
| digraph-without-left-brace | `c_scan.py` | `'r"<:\|:>\|<%\|%>\|%:"'` → `'r"<:\|:>\|%>\|%:"'` | 1 | `C source case failed: <%: []` |
| digraph-without-right-brace | `c_scan.py` | `'r"<:\|:>\|<%\|%>\|%:"'` → `'r"<:\|:>\|<%\|%:"'` | 1 | `C source case failed: %>: []` |
| digraph-without-hash | `c_scan.py` | `'r"<:\|:>\|<%\|%>\|%:"'` → `'r"<:\|:>\|<%\|%>"'` | 1 | `C source case failed: %:: []` |
| token-paste-off | `c_scan.py` | `'if "##" in line:'` → `'if False:'` | 1 | `C source case failed: ##: []` |
| directive-off | `c_scan.py` | `'re.search(r"\\bmapped_mem\\b", match[0])'` → `'False'` | 1 | `C directive case failed: #define MM mapped_mem: []` |
| directive-first-physical-only | `c_scan.py` | `'    text = _splice(text)'` → `'    text = text if re.search(r"(?m)^[ \\t]*#", text) else _splice(text)'` | 1 | `C directive case failed: #define MM \` |
| fixture-text-write | `check-vulkan-buffer-domain.py` | `'(root / "test.patch").write_bytes(patch.encode())'` → `'(root / "test.patch").write_text(patch)'` | 0 | `None; expected 0` |

The following mutations target joined tokens, alias replacements and statement boundaries.

| Mutant | File | Exact replacement (`old` → `new`) | Observed exit | First failing self-test case |
| --- | --- | --- | --- | --- |
| spliced-hazard-off | `c_scan.py` | `'    origins: list[int] = []\n    joined = _splice(text, origins)'` → `'    return\n    origins: list[int] = []\n    joined = _splice(text, origins)'` | 1 | `C source case failed: <\` |
| spliced-removed-joined | `c_scan.py` | `'check_spliced("\\n".join(side), "patch", positions)'` → `'check_spliced("\\n".join(line[1:] for line in lines), "patch")'` | 1 | `C source case failed: removed paste: ['2: unreadable patch token paste']` |
| alias-directive-off | `c_scan.py` | `' or directive_alias(\n            match[0], aliases\n        )'` → `''` | 1 | `C directive case failed: { uint32_t *p = (uint32_t *)b->mapped_mem;` |
| compound-start-flat | `c_scan.py` | `'    start = _statement_boundary(text, pos)'` → `'    start = max(text.rfind(c, 0, pos) for c in ";{}") + 1'` | 1 | `C statement case failed: (struct Host){ .p = host }.p` |
| close-paren-boundary | `c_scan.py` | `'        elif match[0] in ";{}":\n            start = match.end()'` → `'        else:\n            start = match.end()'` | 1 | `C statement start invariant failed: patch call tail ')'` |
| directive-overlap-kept | `c_source.py` | `'                    errors = scan_directives(text, aliases)[1]'` → `'                    errors = scan_directives(text, aliases)[1]\n                    errors = [e for e in errors if added is None or int(e.split(":")[0]) in added]'` | 1 | `C directive patch case failed: '@@ -1 +1,2 @@\n #define MM mapped_mem\n+x = 1;': []` |
| define-space-required | `c_scan.py` | `'(?:\\([^\\n)]*\\))?'` → `'(?:\\([^\\n)]*\\)\|[ \\t]+)'` | 1 | `C directive case failed: { uint32_t *p = (uint32_t *)b->mapped_mem;` |
| ucn-name-off | `c_scan.py` | `'(?:\\w\|\\\\u[0-9a-fA-F]{4}\|\\\\U[0-9a-fA-F]{8})+'` → `'\\w+'` | 1 | `C directive case failed: { uint32_t *p = (uint32_t *)b->mapped_mem;` |
| start-invariant-off | `c_scan.py` | `'        if _statement_boundary(text, pos) > flat:'` → `'        if False:'` | 0 | `None; expected 0` |
| directive-alloc-blank | `c_source.py` | `'                    errors = scan_directives(text, aliases)[1]'` → `'                    text, errors = scan_directives(text, aliases)'` | 1 | `source tree case failed: ['c.patch:6: storage buffer without VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT (memory flags: VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT \| VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)', 'mapped.c:1: mapped_mem in preprocessor directive', 'b.h:1: element-wise host read of mapped device memory: x = ((T *)b->mapped_mem)[0]; copy it out with one memcpy and read the copy (ADR-0255)', 'c.patch:5: element-wise host read of mapped device memory: reader(b->mapped_mem); copy it out with one memcpy and read the copy (ADR-0255)', 'c.patch:9: element-wise host read of mapped device memory: reader(b->mapped_mem); copy it out with one memcpy and read the copy (ADR-0255)']` |
| directive-mapped-unblank | `check-vulkan-buffer-domain.py` | `'mapped_sites(\n            name, scan_directives(text, set())[0], added\n        )'` → `'mapped_sites(name, text, added)'` | 1 | `source tree case failed: ['c.patch:6: storage buffer without VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT (memory flags: VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT \| VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)', 'macro.c:2: storage buffer without VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT (memory flags: VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT \| VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)', 'mapped.c:1: mapped_mem in preprocessor directive', 'b.h:1: element-wise host read of mapped device memory: x = ((T *)b->mapped_mem)[0]; copy it out with one memcpy and read the copy (ADR-0255)', 'c.patch:5: element-wise host read of mapped device memory: reader(b->mapped_mem); copy it out with one memcpy and read the copy (ADR-0255)', 'c.patch:9: element-wise host read of mapped device memory: reader(b->mapped_mem); copy it out with one memcpy and read the copy (ADR-0255)', 'mapped.c:1: element-wise host read of mapped device memory: #define READ(b) ((b)->mapped_mem[0]); copy it out with one memcpy and read the copy (ADR-0255)']` |

Some mutants first emit diagnostics from the live tree; the table identifies their first failing self-test case. The classifier-off mutant first emits a stale analyze exception, then fails `digraph subscript`. The mask-identity mutant first emits a false mapped-read diagnostic for comment text at `vf_pelorus_analyze_vulkan.c:632`, then fails `quoted brace alias`. The earlier-region flag mutant first fails the cross-hunk alias-directive case and also fails `patch hunk call` and `patch hunk flags`. Member-source, context-overlap, allocation-overlap and exact-origin fixtures retain their independent checks.

Equivalent mutants have expected exit 0. `start-invariant-off` removes only the delimiter-boundary assertion; the argument-tail fixtures still reject the boundary regression independently. `malformed-header-metadata` leaves the rejected header to the metadata allow-list; its diagnostic still names the same unreadable line. `patch-segment-CR-optional` restores unreachable CR matching: the reader fixtures reject CR before `patch_segment()` runs, while `mask_c()` retains direct-string CR handling. `keyword-skip` preserves refusal through the pointer-expression guard and statement allow-list: within bulk/sink arguments, keyword parentheses remain invalid pointer expressions; at statement level, only plain cast stores and allow-listed alias declarations pass.

The `fixture-text-write` mutation replaces the checker fixture’s `.write_bytes(patch.encode())` with `.write_text(patch)`: its self-test exits 0 on Linux, where text writes retain LF. The Semgrep fixture test independently detects exactly one finding at `scripts/check-vulkan-buffer-domain.py:488`; its real-tree scan finds none. The named harness supplies temporary settings paths, and direct scans use redirected Semgrep state.

The two harness removals also have expected exit 0. Dropping `reading_self_test()` or `scan_self_test()` removes its assertions while leaving runtime checks intact; a clean live tree therefore cannot detect that missing test call. These are harness observations, not killed rule mutants.

The single rule `pelorus-strict-reader-fixture-bytes` includes `/scripts/c_source.py`, `/scripts/c_scan.py` and `/scripts/check-vulkan-buffer-domain.py`. Its write anchors and mode filter remain shared across those paths. `scripts/test-semgrep-strict-reader-fixture.py` parses all 127 tracked Python files with `ast`: two importers plus the two reader modules form a three-path union, equal to the three paths with findings in a planted tree. An additional planted path, `scripts/new-importer.py`, imports `c_scan` but lies outside the list; the drift self-check requires a mismatch naming it. Git errors, an empty tracked inventory and Python parse errors fail the test; parse diagnostics name the source path.

The importer cases accept 33 import forms and reject 11 non-import forms, plus one invalid-source case requiring a parse failure that names its source path. Statements inside functions, classes, conditionals and `try` blocks count, as do dotted components, package and relative imports, and literal `import_module` or `__import__` calls with any qualifier; the module name is the first positional argument, otherwise the `name` keyword. Aliases alone, similar names, variable module names, other keywords such as `package`, comments and strings do not count. Module names computed at run time and path-based loading with `importlib.util.spec_from_file_location` remain outside this scope.

The 925-case write matrix runs at each of the three included paths and at `scripts/other.py`, an importer outside the list: 3,700 cases yield exactly 1,299 findings and none outside the list. The checker plant has 40,960 bytes of filler before its five-line header and a reader import after the matrix. Restoring the regex scope loses all 433 findings in that checker plant and adds 433 findings in `scripts/other.py`: the total still equals 1,299, so exact path and line equality matters.

Each scope retains the 729 import-resolved calls and adds 196 calls inside one function whose five parameters shadow `TextIOWrapper`, `NamedTemporaryFile`, `TemporaryFile`, `SpooledTemporaryFile` and `fdopen`. The shadowed calls keep their original refusal expectations and have distinct labels. Semgrep 1.183.0 resolves the imported names for qualified patterns; shadowing makes the nine bare-pattern renames independently detectable.

Rule mutants run with `python3 -I scripts/test-semgrep-strict-reader-fixture.py --rule-file "$TMPDIR/<name>/.semgrep.yml"`. Test mutants copy the test and `semgrep_harness.py` into `$TMPDIR/<name>/scripts/` and run the copied test with `--root` pointing at the repository. The test passes that repository to its real-tree steps as `repo_root`, which `scripts/check-semgrep-harness.py` now counts with `ROOT` and `REPO_ROOT` as a repository target: that scan must keep Git ignores (`hermetic=False`), and every planted scan must not. Self-test cases p14 to p16 plant `repo_root` with each flag and a planted `root`. The three write-family mutants remove the named `pattern-either` anchor and its children from a rule copy. No mutation changes the live source tree.

| Semgrep or test mutant | Change in copy | Observed exit | First diagnostic |
| --- | --- | --- | --- |
| include-drop-checker | Remove `/scripts/check-vulkan-buffer-domain.py` from `include` | 1 | `step (a): missing=` checker lines from 2054; 866/1299 findings |
| include-drop-scan | Remove `/scripts/c_scan.py` from `include` | 1 | `step (a): missing=` reader lines from 7; 866/1299 findings |
| finder-top-level | Replace `ast.walk(tree)` with `tree.body` | 1 | `importer cases: missing=` class, conditional, dynamic, nested and try forms |
| finder-no-dynamic | Remove the `ast.Call` branch | 1 | `importer cases: missing=` all seven dynamic forms |
| finder-no-relative | Ignore imported names when `ImportFrom.module` is `None` | 1 | `importer cases: missing=['relative-package'], extra=[]` |
| drift-no-self-check | Require success from the augmented importer comparison | 1 | `drift self-check failed: scripts/new-importer.py` |
| regex-scope-back | Restore the whole-file import regex from `756ec35`; remove the include list | 1 | `step (a): missing=` checker lines from 2054, extra `scripts/other.py` lines; 1299/1299 total |
| fixture-tempfile-off | Remove `strict_reader_tempfile` | 1 | `step (a): missing=` reader lines from 295; 459/1299 findings |
| fixture-fdopen-off | Remove `strict_reader_fdopen` | 1 | `step (a): missing=` reader lines from 169; 1059/1299 findings |
| fixture-text-wrapper-off | Remove `strict_reader_text_wrapper` | 1 | `step (a): missing=` reader lines from 10; 1284/1299 findings |
| drift-inherit-env | Remove `env=scrubbed_env()` from the `tracked_python()` Git call | 1 | `git ls-files` exits 128 under the poisoned `GIT_DIR` |
| finder-no-keyword | Read only the first positional argument of an import call | 1 | `importer cases: missing=` both keyword forms |
| spooled-size-first | `.semgrep.yml`: `'                      - pattern: $X.SpooledTemporaryFile($SIZE, $MODE, ...)\n                      - pattern: $X.SpooledTemporaryFile(..., mode=$MODE, ...)\n                      - pattern: SpooledTemporaryFile($SIZE, $MODE, ...)\n'` → `'                      - pattern: $X.SpooledTemporaryFile($MODE, ...)\n                      - pattern: $X.SpooledTemporaryFile(..., mode=$MODE, ...)\n                      - pattern: SpooledTemporaryFile($MODE, ...)\n'` | 1 | `tempfile.spooled:1024` at `scripts/c_scan.py:37`; extra finding; `hits=1587/1299` |
| spooled-no-positional | `.semgrep.yml`: `'                      - pattern: $X.SpooledTemporaryFile($SIZE, $MODE, ...)\n                      - pattern: $X.SpooledTemporaryFile(..., mode=$MODE, ...)\n                      - pattern: SpooledTemporaryFile($SIZE, $MODE, ...)\n'` → `'                      - pattern: $X.SpooledTemporaryFile(..., mode=$MODE, ...)\n'` | 1 | `tempfile.SpooledTemporaryFile:1024, {mode}:"w"` at `scripts/c_scan.py:547`; missing finding; `hits=1179/1299` |
| bare-text-wrapper-off | `.semgrep.yml`: `'- pattern: TextIOWrapper(...)'` → `'- pattern: NeverTextIOWrapper(...)'` | 1 | `shadowed:bare-text-wrapper` at `scripts/c_scan.py:738`; missing finding; `hits=1296/1299` |
| bare-named-positional-off | `.semgrep.yml`: `'- pattern: NamedTemporaryFile($MODE, ...)'` → `'- pattern: NeverNamedTemporaryFile($MODE, ...)'` | 1 | `shadowed:NamedTemporaryFile:{mode}:"w"` at `scripts/c_scan.py:787`; missing finding; `hits=1269/1299` |
| bare-named-keyword-off | `.semgrep.yml`: `'- pattern: NamedTemporaryFile(..., mode=$MODE, ...)'` → `'- pattern: NeverNamedTemporaryFile(..., mode=$MODE, ...)'` | 1 | `shadowed:NamedTemporaryFile:mode={mode}:"w"` at `scripts/c_scan.py:808`; missing finding; `hits=1269/1299` |
| bare-temporary-positional-off | `.semgrep.yml`: `'- pattern: TemporaryFile($MODE, ...)'` → `'- pattern: NeverTemporaryFile($MODE, ...)'` | 1 | `shadowed:TemporaryFile:{mode}:"w"` at `scripts/c_scan.py:829`; missing finding; `hits=1269/1299` |
| bare-temporary-keyword-off | `.semgrep.yml`: `'- pattern: TemporaryFile(..., mode=$MODE, ...)'` → `'- pattern: NeverTemporaryFile(..., mode=$MODE, ...)'` | 1 | `shadowed:TemporaryFile:mode={mode}:"w"` at `scripts/c_scan.py:850`; missing finding; `hits=1269/1299` |
| bare-spooled-positional-off | `.semgrep.yml`: `'- pattern: SpooledTemporaryFile($SIZE, $MODE, ...)'` → `'- pattern: NeverSpooledTemporaryFile($SIZE, $MODE, ...)'` | 1 | `shadowed:SpooledTemporaryFile:1024, {mode}:"w"` at `scripts/c_scan.py:871`; missing finding; `hits=1269/1299` |
| bare-spooled-keyword-off | `.semgrep.yml`: `'- pattern: SpooledTemporaryFile(..., mode=$MODE, ...)'` → `'- pattern: NeverSpooledTemporaryFile(..., mode=$MODE, ...)'` | 1 | `shadowed:SpooledTemporaryFile:1024, mode={mode}:"w"` at `scripts/c_scan.py:892`; missing finding; `hits=1239/1299` |
| bare-fdopen-positional-off | `.semgrep.yml`: `'- pattern: fdopen($FD, $MODE, ...)'` → `'- pattern: Neverfdopen($FD, $MODE, ...)'` | 1 | `shadowed:bare-fdopen-positional:"w"` at `scripts/c_scan.py:745`; missing finding; `hits=1269/1299` |
| bare-fdopen-keyword-off | `.semgrep.yml`: `'- pattern: fdopen(..., mode=$MODE, ...)'` → `'- pattern: Neverfdopen(..., mode=$MODE, ...)'` | 1 | `shadowed:bare-fdopen-keyword:"w"` at `scripts/c_scan.py:766`; missing finding; `hits=1269/1299` |

### Source mutations

For mutations, copy `ffmpeg-patches/files` into a temporary directory and run `python3 -I scripts/check-vulkan-buffer-domain.py --files <copy>` after each independent change:

| Mutation | Observed result (rerun 2026-11-11) |
| --- | --- |
| m1: Replace `attach_motion(s, in, s->mv_host,` with `attach_motion(s, in, ((const FFVkBuffer *)mvx_ref->data)->mapped_mem,` | Exit 1; mapped-read diagnostic at `vf_pelorus_mc_vulkan.c:808` |
| m2: Append `+ 0` to the analyze known-read expression | Exit 1; new refused site and stale exception (`matches no site; remove the entry`) |
| m3: Remove the analyze entry from a temporary script copy with `c_scan.py`, `c_source.py` and `vulkan_buffer_cases.py` beside it; run that script with `--files` pointing at the real source tree | Exit 1; mapped-read diagnostic at `vf_pelorus_analyze_vulkan.c:710` |
| m4: Point `--files` at a directory containing only `x.c` with `int x;` | Exit 1; both empty-scan diagnostics name the selected directory, not the default |

These checks leave runtime sources and the FFmpeg patch stack unchanged. `REUSE.toml` supplies the existing EUPL-1.2 annotation for these Markdown files.

### Review findings

| Finding | Fix | Planted case |
| --- | --- | --- |
| An unmatched closing parenthesis after an added argument tail hides a context read | Only semicolon and brace delimiters outside argument spans advance the boundary | Argument-tail patch with and without the closing parenthesis; delimiter invariant across every statement fixture |
| A context macro carries an added read through a mapped alias or member name | Refuse mapped directives on the entire new side without added-line overlap | Context alias with added read, context member macro, added alias with context macro and read |
| Macro names with UCNs and adjacent replacement tokens evade alias matching | Consume the complete macro name and an optional immediately adjacent parameter list | Adjacent `+p`, UCN object/function names; accepted `G\u00e4(p)` and `G\U000000e4(p)` formal parameters; unrelated replacement, shadowed parameter and alias-prefix controls |
| Splicing forms `##` | Shared raw/spliced hazard check | C/H, additions, context and removals |
| Splicing forms digraphs | Check before masking | All digraphs; safe macro continuations |
| Macros hide mapped aliases | Refuse replacement-list aliases | Object/function macros, order |
| Compound literals hide the callee | Reuse nested argument bounds | Callee-only patch and control |
| Spooled size treated as mode | Match the second argument | Positional/keyword modes, defaults |
| Imported bare calls also match qualified patterns, leaving bare patterns untested | Duplicate every bare-name case inside one function with parameters shadowing the five imported names | 196 shadowed cases per scope; all nine independent bare-pattern renames fail on missing findings |
| Tuple dispatch mixes call signatures | Direct short-circuit calls | Five checks in order |
| Literal braces and comment markers alter classification | Shared literal/comment masking; malformed constructs retain source | Quoted/character brace aliases, quoted comments, literal mapped token, unterminated character |
| Member callee inherits a bare-function contract | Refuse names preceded by `->` or `.` | Member destination, source and sink |
| Spliced token disappears | Join backslash-newline before masking; restore newline count | Spliced mapped token, CRLF/whitespace and multiple-splice lexical cases |
| Duplicate exception entries pass | Reject repeated `(file, statement)` keys | Duplicate entries with one refused site |
| Bulk return value escapes | Require destination bulk call to be the whole expression statement | Nested result reader, result alias, assignment and condition |
| Keyword accepted as an alias type | Allow-list declaration specifiers and type-name forms | Return/else/do output stores |
| Alias store appears within an expression | Require every use to start a statement | Chained store, call argument, RHS/sum/index reads |
| File-selection behavior lacks integration coverage | Exercise C, header and patch files through `source_texts()` and `check()` | Temporary tree requires exactly `b.h:1`, `c.patch:5`, `c.patch:6`, `c.patch:9`, `macro.c:2` and `mapped.c:1` diagnostics |
| subscript commas shift argument positions | Derive both splitting and indexing from one delimiter-depth scan | Subscript sink input, accepted subscript copy source, C/patch subscript allocations, brace sink input and exact argument spans |
| context blanking changes classification | Keep the full masked new side with splice-aware origins; require statement/tail or allocation-call overlap | Result escape, spliced reader, context alias/open alias, split allocation, context-only reader/allocation, reader argument and added alias |
| cast stripping hides nested mapped access | Require exactly one mapped token in each allowed bulk/sink pointer expression | Nested mapped source, destination and sink; existing nested cast store remains refused |
| a context-line splice absorbs an added read | Carry added provenance to the joined new-side origin, skipping removed lines and markers | `patch context splice read`: context `if (ok)` ending in a splice, added cast read, context `do_something();` |
| Header-shaped removed/added pair terminates a hunk before an added mapped read | Consume declared old/new counts; metadata shapes have no meaning inside a body; report malformed hunks | `patch header-shaped read`, `patch header-shaped argument`, `patch header-shaped then read`; `header-shape-readded` exits 1 |
| Lone CR and backslash-CR inside a patch addition bypass LF-based masking | Strict reader rejects CR before patch parsing or lexing | Embedded CR, CRLF and CR-splice cases assert the unreadable line in each source kind |
| Binary patch sections write unseen C content | Full-line outside-body allow-list rejects binary and other unsupported metadata | `GIT binary patch`, `literal 40`, prefix-match and index-shape plants |
| Trigraph splice joins a mapped token before C lexing | Reject all nine trigraph forms before masking | `??/` splice in C, header and patch addition; non-splice `??=` and `??(` |
| Trigraph escape changes literal termination and exposes a read | Reject trigraphs in literals and comments too | `'??/''` followed by a mapped read in all source kinds |
| Windows CI on `6481f08`: all four Windows fast-suite jobs failed `vulkan-buffer-domain` because fixture text writes introduced CR | Write fixtures as bytes; refuse text-mode writes with the path-scoped Semgrep rule; check out scanned C/H files with LF | `scripts/test-semgrep-strict-reader-fixture.py` checks the parsed importer inventory, explicit path scope, write modes and a reverted fixture |
| both reported examples are accepted; digraph subscript commas shift the mapped input into a write-only argument; a digraph block opener ends alias checking early | Reject raw `<:`, `:>`, `<%`, `%>` and `%:` before lexing | Both reported examples plus every digraph, `%:%:`, literal/comment forms and safe punctuation in `_preprocessing_cases()`; each refused at line 2 |
| `CAT(mapped_, mem)` is accepted; the object-like alias is refused only by generic statement classification | Reject raw `##`; refuse `mapped_mem` in spliced, comment-masked directives at their first line and blank directives before statement classification | Paste call, standalone/object-like directive, continued directive, leading blanks, C/H and patch additions; context directives naming mapped tokens are refused |
| Temporary-file factories, `fdopen` and `TextIOWrapper` escape the fixture rule | Extend the write-pattern list with all three families; literal dynamic imports join the parsed inventory | The matrix covers positional and keyword modes, non-literals, binary modes, defaults, qualified and bare calls; all three anchor-removal mutants exit 1 |
| A measurement with Semgrep 1.183.0 finds no match with 16,018 bytes before the reader import, with `errors: []` | Replace whole-file regex scope with an explicit path list; parse tracked Python to keep it equal to the reader modules plus their importers | At least 32 KiB before the checker matrix; a drift self-check must name the unlisted `scripts/new-importer.py` |
| Git exports `GIT_DIR` and its siblings to hooks; inherited, they make `git -C` list the invoking repository, so the drift inventory can follow the wrong tree | Pass `scrubbed_env()` from `semgrep_harness` to the inventory call | The drift step repeats the inventory with `GIT_DIR` at a missing temporary path, restores it, and requires the same 127 paths; without the scrub Git exits 128 |
| A literal module name passed by keyword (`import_module(name="c_scan")`, `__import__(name="c_source")`) is not an import to the finder | Read the first positional argument, otherwise the `name` keyword | Two keyword import cases; `name=module` and `package="c_scan"` stay non-imports |
| Trigraph `??'` masks a mapped read | Existing refusal; add plant | Quote case below |
| File-mode allow-list accepts non-source objects | Regular-file modes only | Mode cases below |
| Blanking directives for the mapped-read rule also hides allocator calls in `#define` bodies from the allocation rule, in C files and added patch lines | The allocation rule reads the unblanked text; `check()` blanks directives only for the mapped-read classification | `patch macro allocation` refused; `patch device-local macro allocation` and `patch context macro allocation` accepted; `macro.c:2` in the source tree case |

The quote case `x ??'= 1; int x = b->mapped_mem[0]; c = '?';` already produced an unreadable-trigraph error. Its plant, plus one per trigraph form, requires line 2 and the trigraph-specific diagnostic in C, headers and patch additions. Mode cases pass through real patch files. They accept both regular-file modes in `new file mode` and `index`, plus a modeless `index`. They reject new-file modes `120000`, `160000`, `100664`, `040000`, `junk` and an empty value, plus index modes `120000`, `160000` and `100664`, all at metadata line 2.

The measurement on `756ec35` with the expanded write header found 7,176 of 7,488 expected findings: the 666-line late-import plant had 0/312, while the import-first plant had 312/312. An offset measurement retained findings through 8,018 bytes before the import and lost them at 16,018 bytes and above, with no Semgrep errors. PCRE2 10.49 `pcre2test find_limits` measured the following minimum limits; the lazy whole-file loop retains one backtracking frame per scanned character.

| Filler lines | Minimum depth limit | Minimum match limit |
| --- | --- | --- |
| 100 | 1,327 | 7,240 |
| 400 | 5,227 | 28,540 |
| 800 | 10,427 | 56,940 |
| 1,600 | 20,827 | 113,740 |

Rule/source mutation evidence checked at `2026-11-11T18:51:23Z`. NLOC excludes blank and `#` comment lines and includes docstrings. Decision counts include branches, loops, comprehension clauses and filters, conditional expressions, exception handlers and Boolean operators.

| File | SHA-256 | NLOC | Lines over 100 characters | Maximum function lines / decisions |
| --- | --- | --- | --- | --- |
| `check-vulkan-buffer-domain.py` | `102df73144b15112f1f781ebe9b6bcff5086502c33a2ed5a83d09625aaae49b3` | 497 | 39 | 34 / 10 |
| `c_scan.py` | `70b8a34c8a1a7a5d0d32b99a20085f91bfc323096667068ec7feff6abee06409` | 500 | 0 | 45 / 9 |
| `c_source.py` | `f9834ee82a8152f4fbd3ce3c4accb7887d9646c03390738270df697db5b0a36b` | 500 | 0 | 50 / 10 |
| `vulkan_buffer_cases.py` | `f157459522f553d9b3136492373f9a6d6ef3b6231555a0b4cadb1dc4547f2de7` | 43 | 0 | No functions |
| `test-semgrep-strict-reader-fixture.py` | `b7fe595d19ac32abdee28db46cef6da923ea6d11e8b7374ad538635d73adc02d` | 457 | 1 | 47 / 10 |
| `check-semgrep-harness.py` | `693bdb1fc26a804ab814e06fe207a13a105869e05640ef34a2e6fe0a826f474b` | 183 | 0 | 31 / 10 |
| `.semgrep.yml` | `b43a19682265c5b07780761f4a1ad617878968c728e62e9199df9dcfc860e1f2` | 682 | 6 | Not Python |

All six Python files remain within 500 NLOC, 60 physical lines per function and 10 decision points per function. The checker's 39 remaining lines over 100 characters are unchanged; allocation fixtures moved to `vulkan_buffer_cases.py` are wrapped within the limit. Neither supporting module, the data module nor the harness checker has an overlength line. The fixture test's one overlength line is the Bandit and Semgrep marker that `scripts/check-subprocess-markers.py` requires on the start line of its `subprocess.run` call, in the form the other Semgrep tests use. The six overlength YAML lines precede the strict-reader rule and are unchanged.
