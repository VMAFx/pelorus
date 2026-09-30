- Fixed `pel_x265_csv_parse` on Windows to treat its path as UTF-8: it now
  converts strictly to UTF-16 and opens with `_wfopen` instead of decoding
  through the ANSI code page, so non-ASCII report paths open and ill-formed
  UTF-8 returns `PEL_ERR_INVALID` rather than aliasing another file; POSIX
  behavior is unchanged. Those two Windows-only results (`PEL_ERR_INVALID` for
  an ill-formed path, and `PEL_ERR_NOMEM`) are new, so callers should treat any
  result other than `PEL_OK` and `PEL_ERR_RANGE` as a failed read. The
  `pelorus_qp_report` tool passes UTF-8 arguments on Windows, the fast suite
  runs natively under MSYS2 UCRT64 (with a Windows-safe SPIR-V discard target),
  and CI gains a pinned `windows-2025` job that runs it
  ([ADR-0149](docs/adr/0149-windows-utf8-paths.md)).
