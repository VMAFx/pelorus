- Hardened the shared x265 CSV conformance fixture so a permissive process
  umask cannot create group- or world-accessible test data, and pre-existing
  paths cannot be overwritten through the fixed fixture name.
- Made FFmpeg patch replay independent of caller Git identity and configuration
  by pinning an ephemeral committer and neutralizing signing, hooks, and diff
  ordering for every `git am` (ADR-0144).
