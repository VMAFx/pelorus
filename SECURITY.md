# Security policy

## Supported versions

Pelorus is pre-1.0; only the latest `master` and the most recent `v0.x` tag
receive fixes. The interop ABI is append-only, so security fixes never break the
`PelorusSideData` wire format.

## Reporting a vulnerability

Please report privately — do **not** open a public issue for a security bug.

- Preferred: GitHub **Security Advisories** ("Report a vulnerability" on the
  repo's Security tab).
- Or email **lusoris@pm.me** with `[pelorus-security]` in the subject.

Include a reproducer (filtergraph / input characteristics) and the affected
component (a `vf_pelorus_*` filter, `libpelorus`, or the FFmpeg patch stack).
Expect an acknowledgement within a few days.

## Scope notes

- Pelorus runs untrusted **video** through GPU compute shaders and parses
  per-frame side data. The highest-value targets are the side-data parser
  (`libpelorus/src/interop.c` — all offsets/sizes are bounds-checked against the
  buffer before use) and any shader indexing derived from frame dimensions.
- The FFmpeg filters become part of an FFmpeg build; vulnerabilities in FFmpeg
  itself go to the FFmpeg project. Report issues in the Pelorus-authored filter
  code or `libpelorus` here.
