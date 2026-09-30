<!-- markdownlint-disable MD013 MD060 -->
# Windows UTF-8 paths and a native MSYS2 fast-suite leg

**Date:** 2026-09-30

**Decision:** [ADR-0149](../adr/0149-windows-utf8-paths.md)

**Scope:** `libpelorus/src/qp_report_csv.c` (the only path-taking API), the
`pelorus_qp_report` tool, the fast suite under MSYS2 UCRT64, and the CI job
that runs it.

## Finding

`pel_x265_csv_parse` passed its UTF-8 argument to narrow `fopen`. The Windows
CRT decodes a narrow path through the process ANSI code page (`GetACP()` is 1252
on the office workstation). A red run makes the effect concrete: the new
`path-utf8` test compiled against the unfixed reader on Windows fails 13 checks.

- The non-ASCII UTF-8 directory + file (`dír_目录_😀/qp_é_文件_😀.csv`) returns
  `PEL_ERR_ABSENT` although the file exists.
- Six ill-formed UTF-8 names return `PEL_ERR_ABSENT` instead of a
  deterministic error.
- `alias_\xff.csv`, which is ill-formed UTF-8, returns **PEL_OK with the rows of
  the unrelated file `alias_ÿ.csv`**, because cp1252 maps 0xFF to U+00FF. This
  is the aliasing hazard: an ANSI decode can open the wrong file.
- A `\\?\` extended-length path containing `é` fails, again through the
  code-page decode.

The same test passes all checks against the fixed reader.

## Platform facts

`MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, ...)` was probed natively
(MinGW-w64 GCC 16.2, UCRT, Windows 11 26300):

| Input | Result |
|---|---|
| `C3 A9` (é) | 2 units incl. NUL |
| `F0 9F 98 80` (U+1F600) | 3 units (surrogate pair + NUL) |
| `EF BF BF` (U+FFFF noncharacter) | accepted (a valid scalar value) |
| `FF`, stray `80`, overlong `C0 AF` / `E0 80 AF`, encoded surrogates `ED A0 80` / `ED B0 80`, truncated `E6 96`, `F4 90 80 80` (> U+10FFFF), 5-byte `F8 ...` | 0, `GetLastError() == 1113` (`ERROR_NO_UNICODE_TRANSLATION`) |

So the strict flag rejects every ill-formed class. Without it, Vista and later
substitute U+FFFD, which can collide with a real file name.

Other facts that bound the design:

- **Length.** Every Windows path, even `\\?\`, is limited to 32767 UTF-16 code
  units. One unit needs at most 3 UTF-8 bytes (an astral scalar is 4 bytes for
  2 units), so a UTF-8 string over 98301 bytes cannot name a file. A bounded
  scan rejects it before decoding. That keeps the `int` length argument of
  `MultiByteToWideChar` exact and the allocation at 64 KiB or less.
- **Long paths.** `_wfopen` passes a `\\?\` path straight to `CreateFileW`. The
  test opens an extended-length path of about 350 units (the exact length
  depends on the working directory) through the library. Without the
  prefix, paths over `MAX_PATH` work only when the host process opted in
  (`LongPathsEnabled` plus a `longPathAware` manifest). That is a host decision,
  so the reader documents it and does not prefix paths itself.
- **Alternatives that touch process state.** UCRT's `setlocale(LC_ALL, ".UTF8")`
  and the `activeCodePage` manifest both make narrow CRT paths UTF-8. The
  first is process-global; the second belongs to the executable. Neither fits
  a library with no global state.
- **Embedded NUL.** The API takes a NUL-terminated string with no length, so a
  path ends at its first NUL on every platform. There is nothing to reject.
- **Tool arguments.** `main()` receives `argv` in the ANSI code page. The
  demonstrator therefore rebuilds `argv` from `CommandLineToArgvW(GetCommandLineW())`
  with `WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS)`. A CJK + emoji path
  that cp1252 cannot represent at all parses end to end.

## Fast suite under native MSYS2 UCRT64

A baseline run of master's fast suite natively (gcc 16.2, meson 1.12.1,
Python 3.14.7, glslang 16.3.0, shaderc 2026.3) produced:

- **Shader compile checks "passed" only by accident.** A MinGW
  `glslangValidator -o /dev/null` resolves the path to `\dev\null` on the
  current drive. It wrote a real 3260-byte file `C:\dev\null` on the workstation
  (because `C:\dev` happened to exist) and would fail where the directory does
  not exist. The fix discards to `NUL` when the build machine runs Windows.
- **`build-config-sync --self-test` failed in three Git fixtures:**
  1. When a native Windows process starts MSYS2 Git, the MSYS2 runtime globs
     and brace-expands the arguments, so the `n1.2.3^{commit}` argument
     changes. `MSYS=noglob` fixes this case.
  2. The hostile-policy case wraps the same fixture, so it fails the same way.
  3. The `git am` fixture is written through Python text mode, which emits CRLF
     on Windows. `git am` strips the CRs, the patch no longer matches the CRLF
     blob, and the 3-way apply conflicts.

  These fixtures model the Linux-only FFmpeg replay and are being rewritten by
  PR #58, so the Windows leg runs the (platform-neutral, passing) contract
  validation without `--self-test`.
- **Python's UTF-8 mode.** The workstation sets `PYTHONUTF8=1`. The suite also
  passes with the variable unset, which is how the hosted runner starts.

## CI job facts

- `msys2/setup-msys2` latest release is v2.33.0 (2026-09-27), tag →
  `ec48f7c5447b3140e2b088413ae3a55687bccb6e`. It deprecates MINGW32/MINGW64,
  which UCRT64 avoids.
- `windows-2025` is a pinned image label (also `windows-2025-vs2026`), while
  `windows-latest` floats and is rejected by `check-build-config.py`.
- Git for Windows on hosted Windows images converts text to CRLF on checkout
  by default. The job disables `core.autocrlf` before `actions/checkout`, so
  scripts, fixtures, and generated comparisons see the same bytes as the Linux
  jobs.

## Rebase impact

None. No FFmpeg patch consumes `pel_x265_csv_parse`
(`grep -r x265_csv ffmpeg-patches/files` is empty), and no `ffmpeg-patches/files`
input changed, so no patch is regenerated.
