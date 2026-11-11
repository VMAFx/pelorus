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
| `return *out = (T *)b->mapped_mem;`, `else *out = ...`, `do *out = ...` | Refused | Keywords are not allow-listed alias declaration specifiers |
| Alias uses `p[0] = p[1] = 0;`, `f(p[0] = 1);` | Refused | Each use must start a statement; chained writes are unclassified even without a C read |
| Alias uses `p[0] = p[1];`, `p[0] = 1 + p[1];`, `q[p[0]] = 1;` | Refused | RHS and index reads are retained as planted cases |
| Unterminated character literal before `reader(b->mapped_mem);` | Refused | Malformed literal contents remain visible to the classifier |
| Patch addition `/*`, context `*/`, then an added mapped read | Refused | New-side context closes the comment before additions are selected |
| `log("mapped_mem");`, `mapped/**/_mem` | Allowed; zero mapped tokens | Literals cannot fabricate tokens; comments separate tokens |
| `(void)memset(b->mapped_mem, 0, n);` | Allowed | The destination return value is explicitly discarded |
| `vf_pelorus_analyze_vulkan.c:710` | Known, #596 | `base` feeds per-tile reads; cost scales with resolution |
| `vf_pelorus_denoise_vulkan.c:814` | Known, #596 | `acc_out` exposes a fixed 160-word accumulator |
| `vf_pelorus_grain_estimate_vulkan.c:907` | Known, #596 | `attach_estimate()` reads a fixed statistics struct; collapsed statement includes its preceding `if (err >= 0)` |

The scanner is lexical, not a C parser, and performs no preprocessing or macro expansion. Unterminated literals and block comments remain unmasked; C23 `1'000` is treated as an unterminated character literal. Comments retain their width and newlines; splices defer swallowed newlines to the next surviving newline or EOF. The scanner does not follow pointers obtained through `vk->MapMemory` or wrapper implementations. `--files DIR` redirects both source checks to a copy for mutation verification. An input tree with no `mapped_mem` token fails closed.

## Verification

Run the focused checker from the repository root:

```bash
python3 -I scripts/check-vulkan-buffer-domain.py --self-test
python3 -I scripts/check-vulkan-buffer-domain.py
ruff check --no-cache --config ruff.toml scripts/check-vulkan-buffer-domain.py scripts/c_scan.py
ruff format --no-cache --check --config ruff.toml scripts/check-vulkan-buffer-domain.py scripts/c_scan.py
```

The live tree and self-tests exit 0. File integration uses `TemporaryDirectory` with `a.c` (valid storage allocation and copy-out), `b.h` (cast read), `c.patch` (ignored context read, added read at line 5, host-only storage allocation at line 6), and the three unchanged known-read fixtures. It requires exactly those three new diagnostics and no empty-scan failures. Self-tests cover bulk copies and stores, unsafe calls and aliases, patch additions, exact exception coverage, unknown categories, an empty scan, expiry at day 121 and acceptance at day 120. Every planted mapped-case diagnostic is also checked for doubled semicolons. The pinned Ruff (`ruff==0.17.3` in `.config/python-lint/requirements.txt`) reports clean lint and formatting.

### Self-test strength

All 22 independent rule mutants below exited 1 on 2026-11-11. Copy both Python files into `$TMPDIR/<name>/scripts/`, change only the named file, and run `python3 -I <copy>/check-vulkan-buffer-domain.py --self-test --files ffmpeg-patches/files` from the repository root. Each replacement is represented by exact Python string literals (including indentation and escaped newlines); require `text.count(old) == 1` before `text.replace(old, new, 1)`. The twelve earlier mutants and ten review mutants were all rerun against this implementation.

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

The classifier-off mutant first emits a stale analyze exception; mask-identity first emits a false mapped-read diagnostic for comment text at `vf_pelorus_analyze_vulkan.c:632`. Both also fail the quoted-brace self-test listed above. Other mutants report their failing case first. Disabling the member guard is independently caught by the member-qualified source argument; the destination-statement rule also refuses member-qualified destination calls. Disabling patch context is caught by the exact masked-output assertion, even though the unterminated-comment rule keeps the later reader visible.

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

Rule/source mutation evidence checked at `2026-11-11T03:19:42Z`: `check-vulkan-buffer-domain.py` SHA-256 `9460dcb6d1fd833896bf8d916f4f162a098141cbfebe8410aaf6149cd5d834ed`; `c_scan.py` SHA-256 `57fe8d2934cf38c5b03ec2ce0ca3941b3b3c5ed359d9e038c74361db8f6fa41f`. NLOC (nonblank, excluding `#` comment lines, including docstrings): checker 500; scanner 194. Each function stays within 60 physical lines and 10 decision points in the local AST inspection. Repository gates remain the dispatching session's responsibility.
