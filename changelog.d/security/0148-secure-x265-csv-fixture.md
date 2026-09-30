- Hardened the shared conformance fixture's file creation (issues #60, #62;
  ADR-0148). Fixture files are now created exclusively and owner-only from the
  first open: mode `0600` on POSIX under any umask, and a protected owner-only
  DACL on Windows instead of the directory's inherited ACL. An existing file,
  symlink, or dangling symlink at a fixture path is refused and left untouched
  instead of being truncated or followed. A regression covers each property.
- Made FFmpeg patch replay independent of caller Git identity and configuration
  by pinning an ephemeral committer and neutralizing signing, hooks, and diff
  ordering for every `git am` (ADR-0144).
