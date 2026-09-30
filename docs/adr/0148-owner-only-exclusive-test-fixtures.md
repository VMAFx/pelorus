<!-- markdownlint-disable MD013 MD060 -->
# ADR-0148: Conformance-fixture files are exclusive, owner-only, and never follow links

- **Status**: Accepted
- **Date**: 2026-09-30
- **Deciders**: Lusoris
- **Tags**: security, test, interop, windows, vmafx

## Context

The shared interop conformance fixture (`libpelorus/test/interop_test.c`,
ADR-0103) writes an x265 CSV into the test's working directory to exercise
`pel_x265_csv_parse` (ADR-0122). On master `f03badd` it used
`fopen("pelorus_x265_csv_test.csv", "w")`. That call opens with
`O_WRONLY|O_CREAT|O_TRUNC` and mode `0666`, so the ambient umask decides the
file's access: under `umask 000` the fixture is world-writable. The fixed name
also lets an existing file or symlink at that path be truncated and written
through. VMAFx vendors this file byte for byte, and GitHub CodeQL flags the
mirror, so the fix has to land in Pelorus first. Issues #60 and #62 set the
acceptance bar: exclusive creation, owner-only permissions from the first open
whatever the umask, no following or truncating of an existing path or link,
cleanup on every exit path, a regression for the permission and no-follow
properties, equivalent owner-scoped semantics on Windows, and a VMAFx mirror
that regenerates byte-identically.

The first fix on this branch (`e709f4e`, `48dce93`) met the POSIX half with
`open(O_CREAT|O_EXCL, 0600)`, but measurement on Windows 11 showed two gaps in
its `_sopen_s(_O_CREAT|_O_EXCL, _S_IREAD|_S_IWRITE)` path
([research digest](../research/0148-owner-only-exclusive-test-fixtures.md)).
The CRT permission argument only toggles the read-only attribute, so the file
inherits the directory's DACL. In `C:\tmp` that DACL grants `BUILTIN\Users`
read and `Authenticated Users` modify, which is the Windows form of a
permissive umask. `CREATE_NEW` also follows a dangling symbolic link and creates
its target. The POSIX regression covered an existing regular file only, not a
link, and a failure after creation could leave the file behind.

## Decision

Every file the conformance fixture writes goes through one helper with these
properties.

- **POSIX**: `open(path, O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600)`.
  `O_EXCL` refuses any existing name, links included; the umask can only remove
  bits from `0600`.
- **Windows**: `CreateFileA(..., CREATE_NEW, FILE_FLAG_OPEN_REPARSE_POINT)` with
  an explicit security descriptor `D:P(A;;FA;;;OW)`: a protected DACL whose only
  entry grants full access to OWNER RIGHTS, so nothing is inherited from the
  directory. `FILE_FLAG_OPEN_REPARSE_POINT` makes an existing link name, live
  or dangling, fail with `ERROR_FILE_EXISTS`.
- **Cleanup**: a file the helper created is removed if the write or close fails.
  A path the helper refused is never modified or removed. Once the x265 fixture
  exists, every later step falls through to its `remove()`.
- **Regression**: a dedicated case creates a fixture under `umask(0)` (POSIX),
  so the requested mode alone must keep it private, and asserts exact mode
  `0600` via `lstat`, or on Windows a protected one-ACE OWNER RIGHTS DACL on a
  non-reparse file. It then proves that an existing file, a link to it, and a
  dangling link are refused, that the target keeps its bytes, and that nothing
  is created through the dangling link. On Windows the link cases are skipped
  with a note when the account cannot create symbolic links (no Developer Mode
  and no `SeCreateSymbolicLinkPrivilege`).
- **Stale files fail closed**: a fixture path left behind by an aborted run
  fails the test with a message that names it. The test does not delete a file
  it did not create.

The fixture names stay fixed. The file needs no feature macro in its body:
Pelorus's Meson target adds `_POSIX_C_SOURCE=200809L` on non-Windows hosts, and
VMAFx already builds with `_GNU_SOURCE`, because VMAFx's renderer drops
everything before the first `#include "pelorus/...`. Windows links `advapi32`,
which Meson's default `c_winlibs` provides; MSVC also gets
`#pragma comment(lib, "advapi32.lib")`.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| **Exclusive create at a fixed name, 0600 / protected owner DACL, link regression (chosen)** | One primitive on both platforms; every acceptance property has a deterministic regression; refusal is testable because the path is known | A stale file from a killed run fails the next run until someone removes it | Chosen: fail-closed is the correct default for a security fixture, and the failure message names the path |
| `mkstemp()` random name (POSIX), `GetTempFileName` (Windows) | Unpredictable name, so no stale-file collisions | Not in the MSVC CRT; `GetTempFileName` creates with inherited ACLs and needs a second step to restrict them; a random name cannot be pre-planted, so the no-follow property needs a second, fixed-name code path to test | Two creation primitives to keep correct for no security gain once creation is exclusive |
| Keep `_sopen_s(_O_EXCL, _S_IREAD\|_S_IWRITE)` on Windows (branch head before this ADR) | CRT-only, no advapi32 | Inherits the directory DACL (Users read, Authenticated Users modify under `C:\tmp`); follows dangling links | Fails the owner-scoped and no-follow criteria (measured) |
| Create, then `chmod`/`SetFileSecurity` | Familiar pattern | A window in which the file is readable or writable by others; issue #62 rules it out explicitly | Rejected |
| `umask(077)` around the old `fopen("w")` | Minimal diff | Still `O_TRUNC` and follows links; mutates process state; still flagged by CodeQL | Fixes only the mode |
| Write the fixture under a private temporary directory | Hides the path from other users | Directory creation needs the same exclusive, owner-only primitive plus recursive cleanup | More code for the same guarantee |

## Consequences

- **Positive**: fixture creation cannot widen access under any umask or any
  inherited Windows ACL, and cannot write through an existing path or link. Nine
  mutants of the primitives (four POSIX, five Windows) each fail the suite. The
  VMAFx mirror carries the same guarantees on re-vendor.
- **Negative**: the Windows fixture code uses advapi32 security APIs, and the
  link cases need Developer Mode or the symlink privilege to run, and skip
  otherwise. An aborted run leaves a file that fails the next run until someone
  removes it.
- **Neutral / follow-ups**: VMAFx re-pins `PELORUS_VENDOR_SHA` to the merged
  commit and re-vendors with `scripts/sync-pelorus-interop.sh --update`. The
  rendered fixture was checked byte-identical and passing (see the research
  digest). Hosted Windows CI for Pelorus is ADR-0149's scope.

## References

- Issues [#60](https://github.com/VMAFx/pelorus/issues/60) and [#62](https://github.com/VMAFx/pelorus/issues/62); PR [#58](https://github.com/VMAFx/pelorus/pull/58).
- [ADR-0103](0103-interop-sidedata-abi.md) (shared conformance fixture), [ADR-0122](0122-qp-feedback-csv-reader.md) (x265 CSV reader).
- [Research digest 0148](../research/0148-owner-only-exclusive-test-fixtures.md): probes, strace, and mutation results.
- POSIX `open()`: with `O_CREAT` and `O_EXCL`, an existing symbolic link fails with `EEXIST` regardless of its target.
- Win32 `CreateFileA` (`CREATE_NEW`, `FILE_FLAG_OPEN_REPARSE_POINT`), `ConvertStringSecurityDescriptorToSecurityDescriptorA`, SDDL `OW` (OWNER RIGHTS, S-1-3-4).
- Source: `req`: issue #60 "Keep the file owner-only (`0600`) regardless of ambient umask ... an existing path cannot be silently followed or truncated"; issue #62 "Windows keeps equivalent owner-scoped temporary-file semantics".
