<!-- markdownlint-disable MD013 MD060 -->
# ADR-0149: libpelorus path arguments are UTF-8; a native Windows CI leg proves it

- **Status**: Accepted (2026-09-30)
- **Date**: 2026-09-30
- **Deciders**: Lusoris
- **Tags**: windows, portability, api, ci, build

## Context

`pel_x265_csv_parse()` (ADR-0122) is the only libpelorus function that takes a
filesystem path. It opened the caller's string with narrow `fopen(path, "r")`.
On POSIX that string is a byte sequence the kernel uses as-is. On Windows the
narrow CRT decodes it through the process ANSI code page (cp1252 on this
project's Windows workstation), so a valid UTF-8 path such as
`dír_目录_😀/qp_é_文件_😀.csv` either fails with `PEL_ERR_ABSENT` although the
file exists, or, worse, resolves to a different file: the ill-formed UTF-8 name
`alias_\xff.csv` is `alias_ÿ.csv` under cp1252, and the old reader opened that
file and returned its rows. Issue #61 tracks the defect; it is the
Pelorus-owned instance of the path-encoding class in Netflix/vmaf#1568, and
VMAFx vendors `qp_report_csv.c` byte-for-byte, so the fix must remain
dependency-free and self-contained.

Two further facts shape the fix. A library must not change process-global
state, so switching the CRT locale to UTF-8 or depending on an application
manifest is the host's choice, not the library's. And the fast suite had never
run on Windows: the Linux jobs cannot execute Windows-only assertions, and a
native MSYS2 UCRT64 run of the suite found two portability defects unrelated to
the reader. The shader compile checks wrote their discarded SPIR-V to
`/dev/null`, which a MinGW `glslangValidator` resolves to a real file
`\dev\null` on the current drive. The build-contract self-test drives Git
fixtures that do not survive a native Windows Python (see the research digest).
ADR-0144 put every hosted job on `ubuntu-26.04`; a Windows job needs an
explicit exception to that policy.

## Decision

Every libpelorus parameter that names a file is a NUL-terminated UTF-8 string on
every platform. The reader opens it through one static helper in
`qp_report_csv.c`:

- **POSIX**: `fopen(path, mode)` exactly as before, with the same
  `PEL_ERR_ABSENT` on failure. Bytes are passed through unvalidated, so a
  non-UTF-8 byte name still opens.
- **Windows**: the mode string is widened as ASCII (non-ASCII or 16+ characters:
  `PEL_ERR_INVALID`). The path is measured with a bounded scan, converted with
  `MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, ...)` into one heap buffer,
  and opened with `_wfopen`. The buffer is freed before the function returns.
  The checks run in a fixed order, so results are deterministic whatever the
  active code page:
  1. More than 98301 bytes (3 × 32767), which cannot be any Windows path:
     `PEL_ERR_ABSENT`, `errno = ENAMETOOLONG`, and the path is not decoded.
  2. Ill-formed UTF-8 (stray or truncated sequences, overlong forms, encoded
     surrogates, values above U+10FFFF): `PEL_ERR_INVALID`, `errno = EILSEQ`.
  3. More than 32767 UTF-16 code units: `PEL_ERR_ABSENT`, `errno = ENAMETOOLONG`.
  4. Allocation failure: `PEL_ERR_NOMEM`.
  5. `_wfopen` failure for any reason: `PEL_ERR_ABSENT`, the pre-existing
     contract. `errno` from the open is preserved across the `free`.

  The helper adds no `\\?\` prefix and applies no normalization. A caller that
  needs a path longer than `MAX_PATH` passes an extended-length UTF-8 path, or
  enables long paths process-wide (the `LongPathsEnabled` policy plus a
  `longPathAware` manifest). Nothing in the conversion stops at 260 units.

The public headers (`pelorus.h`, `interop.h`) and `docs/api/interop-abi.md` state
the contract. The `pelorus_qp_report` demonstrator rebuilds its `argv` as UTF-8
from the UTF-16 command line on Windows, so it keeps the contract too. There is
no struct, signature, or wire change, so `PELORUS_ABI_MINOR` stays at 3 (the
bump-abi rules bump only on appended fields or sections).

CI gains a `windows` job in `ci.yml`, an explicit exception to ADR-0144's runner
policy for this one job:

- `runs-on: windows-2025` (pinned image, never `windows-latest`).
- `msys2/setup-msys2` pinned by commit (v2.33.0) with `msystem: UCRT64`,
  `update: false`, and exactly six packages: gcc, meson, ninja, python,
  glslang, and shaderc.
- Checkout with `core.autocrlf false`, so the job builds the same bytes the
  Linux jobs build.
- `meson setup build && ninja -C build`, then
  `meson test -C build --suite=fast --print-errorlogs`, then a verbose replay of
  the `path-utf8` test as a readable transcript.

`scripts/check-build-config.py` validates all of this. Its `--self-test` rejects
nine mutations of the job.

The fast suite becomes portable in two places:

- The SPIR-V discard target is `NUL` when the build machine runs Windows.
- On Windows, `build-config-sync` runs the contract validation without
  `--self-test`, because the Git-fixture half models the Linux-only FFmpeg
  replay and the Linux jobs run it on every PR.

The new `path-utf8` fast test covers the contract. It is compiled everywhere,
asserts the non-ASCII round trip, the ill-formed and aliasing cases, and a
`\\?\` path past `MAX_PATH` on Windows, and asserts the literal pass-through on
POSIX.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| **Static UTF-8 → UTF-16 opener in `qp_report_csv.c` + `_wfopen`** | Deterministic on every code page; no process-global state; keeps the vmafx mirror to the same file list; POSIX untouched | One bounded heap allocation per call on Windows; Windows-only code the Linux linters never see | **Chosen**: the only option that fixes the library itself without touching host state |
| New shared `pel_path.c` helper for future path APIs | Reusable | Grows the vmafx mirror list and adds a file for one call site | Rejected for now: a second path-taking API can move the static helper out then |
| `setlocale(LC_ALL, ".UTF8")` (UCRT UTF-8 locale) before `fopen` | Narrow CRT becomes UTF-8 | Process-global and not thread-safe; changes every other CRT call in the host | Rejected: violates "no global mutable state" |
| Require an `activeCodePage=UTF-8` manifest in every host | No library code | Only the executable can set it (Windows 10 1903+); a library cannot enforce it; silently wrong otherwise | Rejected: shifts a correctness contract onto every host |
| Lenient conversion (no `MB_ERR_INVALID_CHARS`, U+FFFD substitution) | Never fails on bad bytes | Ill-formed input can alias a different, existing file; still ACP-independent only by luck | Rejected: the issue requires a deterministic failure |
| Hand-written strict UTF-8 decoder | Testable on Linux too | Re-implements a security-sensitive decoder the OS already provides | Rejected: `MultiByteToWideChar` is the platform's own strict decoder |
| Automatic `\\?\` prefixing + `GetFullPathNameW` | Long paths "just work" | Changes relative-path, `..`, and forward-slash semantics; more code and a second allocation | Rejected: out of scope; documented as a caller choice |
| Fixed 64 KiB stack buffer instead of the heap | No allocation | 64 KiB stack in a library call breaks hosts with small thread stacks | Rejected: the bounded heap copy is safer; `PEL_ERR_NOMEM` is documented |
| Skip the Windows CI job; test only locally | No runner cost | The Windows-gated assertions would never run in CI | Rejected: the user decided to run the Windows-gated test in CI |
| Windows job on MSVC / `windows-latest` | Native toolchain | No glslang/meson parity; a floating image breaks reproducibility (ADR-0144) | Rejected: MSYS2 UCRT64 matches the supported MinGW toolchain on a pinned image |
| Port the `--self-test` Git fixtures to Windows now | Full self-test everywhere | PR #58 rewrites exactly those fixtures; porting now guarantees conflicts | Deferred: follow-up after #58 lands |

## Consequences

- **Positive**: non-ASCII report paths work on Windows regardless of the code
  page; ill-formed UTF-8 can no longer open an unrelated file; POSIX behavior is
  byte-for-byte unchanged; the fast suite runs natively on Windows in CI, which
  also caught and fixed the `/dev/null` shader-discard defect.
- **Negative**: on Windows, `pel_x265_csv_parse` performs one bounded (≤ 64 KiB)
  allocation and can return `PEL_ERR_NOMEM`. The Windows branch is invisible
  to the Linux clang-tidy and sanitizer jobs; it is covered by the MinGW
  `-Werror` build and the Windows `path-utf8` test only. The Windows job adds
  hosted-runner time. A `setup-msys2` digest bump from Renovate must also update
  `SETUP_MSYS2_COMMIT` in `scripts/check-build-config.py`, as with `setup-go`.
- **Neutral / follow-ups**: VMAFx should re-sync its mirrored
  `pelorus_qp_report_csv.c`, `interop.h`, and `pelorus.h`. `path_utf8_test.c` is
  Pelorus-only unless VMAFx adds it to its mirror list. Making the
  `check-build-config.py --self-test` Git fixtures Windows-portable is a
  follow-up for after #58 merges.

## References

- Issue #61 (this defect and its acceptance evidence); Netflix/vmaf#1568; VMAFx
  `docs/state.md` row `T-UPSTREAM-1568-WINDOWS-NARROW-PATH-API-2026-09-03`.
- [ADR-0122](0122-qp-feedback-csv-reader.md) (the reader), [ADR-0103](0103-interop-sidedata-abi.md) (ABI rules),
  [ADR-0144](0144-ffmpeg-pin-and-ci-runner-policy.md) (runner policy this ADR amends for one job).
- Microsoft Learn: `MultiByteToWideChar` (`MB_ERR_INVALID_CHARS`,
  `ERROR_NO_UNICODE_TRANSLATION`); `_wfopen`; "Maximum Path Length Limitation";
  "Use UTF-8 code pages in Windows apps" (`activeCodePage`); UCRT `setlocale`
  UTF-8 support.
- `msys2/setup-msys2` v2.33.0 (`ec48f7c5447b3140e2b088413ae3a55687bccb6e`).
- Research digest: [docs/research/0149-windows-utf8-paths.md](../research/0149-windows-utf8-paths.md).
- Source: `req`: issue #61's acceptance list ("A Windows-gated test writes and
  reads a qp-report CSV whose directory and filename contain non-ASCII
  characters ... Invalid UTF-8 fails deterministically ... POSIX behavior
  remains byte-for-byte unchanged"). The user also decided, as relayed by the
  orchestrator, to add an MSYS2 UCRT64 Windows CI job so that the
  Windows-gated test runs in CI.
