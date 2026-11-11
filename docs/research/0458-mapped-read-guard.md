<!-- markdownlint-disable MD013 MD060 -->
# Mapped-memory host-read regression guard

**Date:** 2026-11-11

**Decision:** [ADR-0255](../adr/0255-mapped-memory-host-reads.md).

**Scope:** `scripts/check-vulkan-buffer-domain.py`, fast test `vulkan-buffer-domain`, review baseline `b6d6f84528497f91260547e5748585abcd80583f`, issues #458 and #596. Source evidence comes from C/H files and the added side of `ffmpeg-patches/files/*.patch`; line splicing precedes comment/literal masking. Patch context participates in masking, then is blanked before classification.

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
| Patch addition `/*`, context `*/`, then an added mapped read | Refused | New-side context closes the comment before additions are selected |
| First hunk adds `/*`; second hunk adds a mapped read followed by context `*/` | Refused | The `@@` reset prevents the second hunk's context from closing a comment in the first hunk and hiding the read |
| `log("mapped_mem");`, `mapped/**/_mem` | Allowed; zero mapped tokens | Literals cannot fabricate tokens; comments separate tokens |
| `(void)memset(b->mapped_mem, 0, n);` | Allowed | The destination return value is explicitly discarded |
| `{ T *p = (T *)b->mapped_mem; p[0] = 1; }` | Allowed; one mapped token | The `alias first store` case accepts the first use with an empty statement prefix |
| `{ if (x) { y = 1; } ((uint32_t *)b->mapped_mem)[0] = 0; }` | Allowed | The `store after block` case starts its statement after the closing brace |
| Patch source `@@\n+++ i;` | Added code `\n++ i;` | The `added pre-increment` case retains a spaced increment without a preceding `---` header |
| Plain alias stores after a loop block, an `if` condition, `else` and `do` | Allowed | The `write alias` case exercises statement starts after `}`, `)` and the two keywords |
| `{ T *a = (T *)b->mapped_mem; struct s *c = (struct s *)b->mapped_mem; int *d = (int *)b->mapped_mem; a[0] = 1; c[0] = 1; d[0] = 1; }` | Allowed | Three mapped tokens cover uppercase, tagged and scalar type-name groups; the live denoise scan covers `_t` names |
| `vf_pelorus_analyze_vulkan.c:710` | Known, #596 | `base` feeds per-tile reads; cost scales with resolution |
| `vf_pelorus_denoise_vulkan.c:814` | Known, #596 | `acc_out` exposes a fixed 160-word accumulator |
| `vf_pelorus_grain_estimate_vulkan.c:907` | Known, #596 | `attach_estimate()` reads a fixed statistics struct; collapsed statement includes its preceding `if (err >= 0)` |

The scanner is lexical, not a C parser, and performs no preprocessing or macro expansion. Unterminated literals and block comments remain unmasked; C23 `1'000` is treated as an unterminated character literal. Comments retain their width and newlines; splices defer swallowed newlines to the next surviving newline or EOF. The scanner does not follow pointers obtained through `vk->MapMemory` or wrapper implementations. `--files DIR` redirects both source checks to a copy for mutation verification. An input tree with no `mapped_mem` token fails closed.

## Verification

Patch lexical state resets at `@@` hunk markers. A `+++` file header is recognized only immediately after a `---` line; `+++i;` remains added code. Removed and metadata lines are blank on the new side, so separate `diff`, `index` and `---` reset rules are unnecessary. Generic statement-boundary helpers and the depth-one comma counter `argument_index()` are shared through `scripts/c_scan.py`; the checker imports the counter beside `call_args()` without changing its behavior.

Run the focused checker from the repository root:

```bash
python3 -I scripts/check-vulkan-buffer-domain.py --self-test
python3 -I scripts/check-vulkan-buffer-domain.py
ruff check --no-cache --config ruff.toml scripts/check-vulkan-buffer-domain.py scripts/c_scan.py
ruff format --no-cache --check --config ruff.toml scripts/check-vulkan-buffer-domain.py scripts/c_scan.py
```

The live tree and self-tests exit 0. File integration uses `TemporaryDirectory` with `a.c` (valid storage allocation and copy-out), `b.h` (cast read), `c.patch` (ignored context read, added read at line 5, host-only storage allocation at line 6), and the three unchanged known-read fixtures. It requires exactly those three new diagnostics and no empty-scan failures. Self-tests cover bulk copies and stores, unsafe calls and aliases, patch additions, exact exception coverage, unknown categories, an empty scan, expiry at day 121 and acceptance at day 120. Every planted mapped-case diagnostic is also checked for doubled semicolons. The pinned Ruff (`ruff==0.17.3` in `.config/python-lint/requirements.txt`) reports clean lint and formatting.

### Self-test strength

All 35 independent rule mutants below exited 1 on 2026-11-11. Copy both Python files into `$TMPDIR/<name>/scripts/`, change only the named file, and run `python3 -I <copy>/check-vulkan-buffer-domain.py --self-test --files ffmpeg-patches/files` from the repository root. Each replacement is represented by exact Python string literals (including indentation and escaped newlines); require `text.count(old) == 1` before `text.replace(old, new, 1)`. The table lists 22 mutants from the first review round, ten for the bulk-suffix, statement-start, type-group, block-nesting, literal-newline and hunk-reset rules, and three for the empty-prefix, closing-brace and spaced-increment boundaries; all were rerun against these final Python files. Each of the last three exited 0 before its planted case was added and now exits 1.

| Mutant | File | Exact replacement (`old` → `new`) | Observed exit | First failing self-test case |
| --- | --- | --- | --- | --- |
| classifier-off | `check-vulkan-buffer-domain.py` | `'if not allowed:'` → `'if False:'` | 1 | `mapped case failed: quoted brace alias` |
| store-lookahead | `check-vulkan-buffer-domain.py` | `'=(?!=)"'` → `'="'` | 1 | `mapped case failed: write alias equality` |
| single-mapped | `check-vulkan-buffer-domain.py` | `'if len(re.findall(r"\\bmapped_mem\\b", statement)) != 1:'` → `'if False:'` | 1 | `mapped case failed: store with mapped RHS` |
| const-rule | `check-vulkan-buffer-domain.py` | `'if not alias or re.search(r"\\bconst\\b", alias[1] + " " + alias[3]):'` → `'if not alias:'` | 1 | `mapped case failed: const alias store` |
| pointer-expression | `check-vulkan-buffer-domain.py` | `'return re.fullmatch(CHAIN, plain) is not None'` → `'return True'` | 1 | `mapped case failed: offset` |
| parenthesis-merge | `check-vulkan-buffer-domain.py` | `'    if re.search(r"[\\w)\\]]\\s*\\(", plain):\n        return False\n'` → `''` | 1 | `mapped case failed: parenthesised reader` |
| expiry | `check-vulkan-buffer-domain.py` | `'today > expiry'` → `'today >= expiry'` | 1 | `known-read case failed: boundary` |
| sink-index | `check-vulkan-buffer-domain.py` | `'index == SINKS.get(name, (-1, ""))[0]'` → `'name in SINKS'` | 1 | `mapped case failed: sink input` |
| mapped-fail-closed | `check-vulkan-buffer-domain.py` | `'if mapped_total == 0:'` → `'if False:'` | 1 | `empty tree accepted: no mapped_mem use found` |
| duplicate-sites | `check-vulkan-buffer-domain.py` | `'if count != 1:'` → `'if count == 0:'` | 1 | `known-read case failed: duplicate` |
| alias-uses | `check-vulkan-buffer-domain.py` | `'    return all(\n        re.match(STORE, tail[use.end() :]) and statement_start(tail, use.start())\n        for use in uses\n    )'` → `'    return True'` | 1 | `mapped case failed: quoted brace alias` |
| semicolon | `check-vulkan-buffer-domain.py` | `"{statement.removesuffix(';')}"` → `'{statement}'` | 1 | `doubled semicolon: quoted brace alias` |
| mask-identity | `c_scan.py` | `'    text = _splice(text)'` → `'    return text\n    text = _splice(text)'` | 1 | `mapped case failed: quoted brace alias` |
| literals-off | `c_scan.py` | `'elif text[pos] in "\\"\'":'` → `'elif False:'` | 1 | `mapped case failed: quoted brace alias` |
| splices-off | `c_scan.py` | `'    text = _splice(text)'` → `'    text = text'` | 1 | `mapped case failed: spliced token` |
| unterminated-unmask-off | `c_scan.py` | `'return text[start:pos], pos'` → `'return text[start] + " " * (pos - start - 1), pos'` | 1 | `C masking case failed: unterminated character` |
| patch-context-off | `c_scan.py` | `'line.startswith(("+", " "))'` → `'line.startswith("+")'` | 1 | `C patch case failed: context closes comment` |
| member-guard-off | `check-vulkan-buffer-domain.py` | `'("" if member else name[1])'` → `'name[1]'` | 1 | `mapped case failed: member dot source` |
| bulk-statement-off | `check-vulkan-buffer-domain.py` | `'if name in BULK and index == 0:'` → `'if False:'` | 1 | `mapped case failed: memset result reader` |
| type-allowlist-off | `check-vulkan-buffer-domain.py` | `'({TYPE}(?:\\s+{TYPE})*)'` → `'(\\w+(?:\\s+\\w+)*)'` | 1 | `mapped case failed: return output` |
| alias-statement-off | `check-vulkan-buffer-domain.py` | `' and statement_start(tail, use.start())'` → `''` | 1 | `mapped case failed: chained alias store` |
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
| hunk-reset-off | `c_scan.py` | `'line.startswith("@@") or new_header'` → `'new_header'` | 1 | `C patch case failed: segment reset` |
| statement-empty-prefix-off | `c_scan.py` | `'not prefix or prefix[-1]'` → `'prefix and prefix[-1]'` | 1 | `mapped case failed: alias first store` |
| statement-at-close-brace-off | `c_scan.py` | `'for delimiter in ";{}"'` → `'for delimiter in ";{"'` | 1 | `mapped case failed: store after block` |
| new-header-previous-off | `c_scan.py` | `' and i > 0 and lines[i - 1].startswith("--- ")'` → `''` | 1 | `C patch case failed: added pre-increment` |

The classifier-off mutant first emits a stale analyze exception; mask-identity first emits a false mapped-read diagnostic for comment text at `vf_pelorus_analyze_vulkan.c:632`. Both also fail the quoted-brace self-test listed above. Other mutants report their failing case first. Disabling the member guard is independently caught by the member-qualified source argument; the destination-statement rule also refuses member-qualified destination calls. Disabling patch context is caught by the exact masked-output assertion, even though the unterminated-comment rule keeps the later reader visible.

The keyword-skip equivalent mutant replaces `'if name:'` with `'if name and name[1] not in {"if", "while", "for", "switch", "return", "sizeof"}:'` in `check-vulkan-buffer-domain.py`. Rerunning the same self-test command exits 0 with no diagnostics. Two independent rules preserve refusal: within a bulk or sink argument, the parenthesis-merge guard rejects keyword expressions such as `sizeof(`; at statement level, only plain cast stores and allow-listed alias declarations pass. The survivor therefore does not establish missing keyword-refusal coverage.

### Source mutations

For mutations, copy `ffmpeg-patches/files` into a temporary directory and run `python3 -I scripts/check-vulkan-buffer-domain.py --files <copy>` after each independent change:

| Mutation | Observed result (rerun 2026-11-11) |
| --- | --- |
| m1: Replace `attach_motion(s, in, s->mv_host,` with `attach_motion(s, in, ((const FFVkBuffer *)mvx_ref->data)->mapped_mem,` | Exit 1; mapped-read diagnostic at `vf_pelorus_mc_vulkan.c:808` |
| m2: Append `+ 0` to the analyze known-read expression | Exit 1; new refused site and stale exception (`matches no site; remove the entry`) |
| m3: Remove the analyze entry from a temporary script copy with `c_scan.py` beside it; run that script with `--files` pointing at the real source tree | Exit 1; mapped-read diagnostic at `vf_pelorus_analyze_vulkan.c:710` |
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
| F8: File-selection behavior lacks integration coverage | Exercise C, header and patch files through `source_texts()` and `check()` | Temporary tree requires exactly `b.h:1`, `c.patch:5` and `c.patch:6` diagnostics |

Rule/source mutation evidence checked at `2026-11-11T03:44:06Z`: `check-vulkan-buffer-domain.py` SHA-256 `156e15163dc452db04e2c1848de36fb812546910432cfdb7c55ba6fd7bc6c89d`; `c_scan.py` SHA-256 `c266c4c6a3d4b43992c19ff0c69a5462159cbaad2f563e6b6540edacd0fcbe2a`. NLOC (nonblank, excluding `#` comment lines, including docstrings): checker 490; scanner 219. Local AST inspection counts branches, loops, comprehension clauses, conditional expressions, exception handlers and Boolean operators: the checker has at most 26 physical lines and 9 decision points per function; the scanner has at most 45 physical lines and 6 decision points per function. Both remain within the 60-line and 10-decision limits.
