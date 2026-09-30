<!-- markdownlint-disable MD013 MD060 -->
# Owner-only, exclusive conformance-fixture files

**Date:** 2026-09-30

**Decision:** [ADR-0148](../adr/0148-owner-only-exclusive-test-fixtures.md)

**Scope:** `libpelorus/test/interop_test.c` (the conformance fixture that VMAFx
vendors) on Linux (glibc), native Windows 11 (MSYS2 UCRT64 gcc 16.2 and MSVC
19.44 from VS 2022 17.14), and the VMAFx mirror renderer. Issues #60 and #62.

## Finding

Master `f03badd` wrote the x265 CSV fixture with `fopen(path, "w")`. Run under
`umask 000` in the Linux CI image (Ubuntu 26.04, strace 6.19), the call is:

```text
openat(AT_FDCWD, "pelorus_x265_csv_test.csv", O_WRONLY|O_CREAT|O_TRUNC, 0666) = 3
```

That is world-writable under a permissive umask, and it truncates and writes
through whatever already sits at the fixed name, symlinks included.

The branch's first fix used `O_CREAT|O_EXCL` with `0600` on POSIX, which is
sound, and `_sopen_s(_O_CREAT|_O_EXCL, _S_IREAD|_S_IWRITE)` on Windows. Two
probes on this Windows 11 host (`C:\tmp\pel\s58-scratch\*.c`, compiled with
UCRT64 gcc) showed that the Windows half did not meet the issues:

| Probe | Result |
| --- | --- |
| DACL of a `_sopen_s` file created under `C:\tmp\...` | `D:AI(A;ID;FA;;;BA)(A;ID;FA;;;SY)(A;ID;0x1200a9;;;BU)(A;ID;0x1301bf;;;AU)`: inherited, `BUILTIN\Users` read/execute, `Authenticated Users` modify |
| DACL of a file created with an explicit `D:P(A;;FA;;;OW)` | `D:P(A;;FA;;;OW)`: protected, owner only; reopen for read and delete both succeed |
| `_sopen_s(_O_CREAT\|_O_EXCL)` on a dangling symlink | opens, and creates the link target |
| `CreateFileA(CREATE_NEW)` on a live symlink | refused, `ERROR_FILE_EXISTS` (80) |
| `CreateFileA(CREATE_NEW)` on a dangling symlink | opens, and creates the link target |
| same with `FILE_FLAG_OPEN_REPARSE_POINT` | refused, `ERROR_FILE_EXISTS` (80), for both live and dangling links; the target is untouched |

The CRT's permission argument only sets the read-only attribute, so a
`_sopen_s` file takes the directory's inherited DACL. Under `C:\tmp`, or any
directory created at a drive root, that DACL lets every local user read the
file and every authenticated user modify it. `CREATE_NEW` treats a dangling
link as absent and follows it. Both gaps are the Windows forms of the problems
that #60 describes for POSIX.

## Implemented primitive

| Platform | Create | Owner-only check in the regression |
| --- | --- | --- |
| POSIX | `open(O_WRONLY\|O_CREAT\|O_EXCL\|O_NOFOLLOW\|O_CLOEXEC, 0600)` under `umask(0)` | `lstat`: regular file, mode exactly `0600` |
| Windows | `CreateFileA(GENERIC_WRITE, share 0, CREATE_NEW, FILE_FLAG_OPEN_REPARSE_POINT)` with the descriptor `D:P(A;;FA;;;OW)` | not a reparse point; DACL protected; exactly one allow ACE, for OWNER RIGHTS (`IsWellKnownSid(WinCreatorOwnerRightsSid)`) |

A write or close failure after creation removes the file. A refused path is
left alone. The test sets `umask(0)` around creation so that the requested mode
alone must keep the file private, which makes the regression independent of
the runner's umask. The Linux trace of the fixed binary under `umask 000`:

```text
umask(000) = 000
openat(AT_FDCWD, "pelorus_fixture_plant.tmp", O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600) = 3
openat(AT_FDCWD, "pelorus_fixture_plant.tmp", O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600) = -1 EEXIST
symlink("pelorus_fixture_plant.tmp", "pelorus_fixture_link.tmp") = 0
openat(AT_FDCWD, "pelorus_fixture_link.tmp", O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600) = -1 EEXIST
symlink("pelorus_fixture_absent.tmp", "pelorus_fixture_dangling.tmp") = 0
openat(AT_FDCWD, "pelorus_fixture_dangling.tmp", O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600) = -1 EEXIST
openat(AT_FDCWD, "pelorus_fixture_absent.tmp", O_RDONLY) = -1 ENOENT
openat(AT_FDCWD, "pelorus_x265_csv_test.csv", O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600) = 3
unlink("pelorus_x265_csv_test.csv") = 0
```

No fixture file is left in the working directory after the run.

## Mutation results

Each mutant weakens one primitive in a copy of the test and runs it. A mutant is
killed when the suite exits non-zero.

| Platform | Mutant | Result | Failing check |
| --- | --- | --- | --- |
| Linux | mode `0600` → `0666`, runner `umask 000` | killed | owner-only check on both fixtures |
| Linux | mode `0600` → `0666`, runner `umask 077` | killed | same, because the test forces `umask(0)` |
| Linux | drop `O_EXCL` | killed | existing file rewritten; link target rewritten |
| Linux | `O_TRUNC` instead of `O_EXCL\|O_NOFOLLOW` | killed | existing file and link target truncated |
| Linux | control: mode `0666` with the forced umask changed to `077` | passes | shows why the test forces `umask(0)` |
| Windows | DACL not protected (`D:(A;;FA;;;OW)`) | killed | inherited ACEs appear |
| Windows | extra `BUILTIN\Users` read ACE | killed | second ACE |
| Windows | no security descriptor (`NULL` attributes) | killed | inherited DACL |
| Windows | drop `FILE_FLAG_OPEN_REPARSE_POINT` | killed | dangling link followed, target created |
| Windows | `OPEN_ALWAYS` instead of `CREATE_NEW` | killed | existing file and link target rewritten |

A stale `pelorus_x265_csv_test.csv` holding foreign bytes makes the test fail
with `exclusive create refused (stale file from an aborted run?)`. The foreign
bytes stay unchanged on both platforms.

On Windows the link cases need Developer Mode or
`SeCreateSymbolicLinkPrivilege`. This host has Developer Mode, so they ran. A
build that simulates `ERROR_PRIVILEGE_NOT_HELD` prints
`note: no symlink privilege; fixture link cases skipped` and still passes.

## Portability

- The body needs `O_NOFOLLOW`, `O_CLOEXEC`, `lstat` and `symlink`, which
  glibc hides under strict `-std=c11`. The Pelorus Meson target defines
  `_POSIX_C_SOURCE=200809L` on non-Windows hosts. The file itself carries no
  feature macro, because VMAFx's renderer keeps only the text from the first
  `#include "pelorus/...` onward; VMAFx builds with `_GNU_SOURCE` on Linux.
- Windows needs `advapi32`. Meson's default `c_winlibs` links it for both GCC
  and MSVC. For non-Meson MSVC builds the file adds
  `#pragma comment(lib, "advapi32.lib")` under `_MSC_VER`. Without it, a bare
  `cl` + `link` build fails with six unresolved `advapi32` symbols.
- MSVC 19.44 compiles the file at `/std:c11 /W4 /WX` and the fixture passes.

## VMAFx mirror

With VMAFx `97daec4` (sparse clone), changing `PELORUS_VENDOR_SHA` to this
branch's fixture commit and running `scripts/sync-pelorus-interop.sh --update`
followed by the check mode reports no drift (`OK: ... ABI 1.3, minor=3`). A
second, independent `--update` produces byte-identical files. The rendered
`core/test/test_pelorus_interop.c` ends with the Pelorus body verbatim, with
only the include rewrite applied, and it builds with `gcc -std=c2x
-D_GNU_SOURCE -Werror` in the VMAFx include layout. It passes under
`umask 000` and leaves no file behind. The VMAFx side still has to re-pin and
re-vendor after this merges.

## Reproduce

```sh
# Linux (#60 reproducer): the fixture asserts 0600 while it runs.
umask 000
meson setup build && meson test -C build interop-abi --print-errorlogs
find build \( -name 'pelorus_x265_csv_test.csv' -o -name 'pelorus_fixture_*' \) -print  # prints nothing

# Windows (MSYS2 UCRT64), from a directory whose inherited ACL is permissive:
meson setup build-win && meson test -C build-win interop-abi --print-errorlogs
```
