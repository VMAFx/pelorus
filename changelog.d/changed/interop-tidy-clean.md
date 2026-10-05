- The interop sources and their conformance test now pass clang-tidy at the
  VMAFx profile, which lints the vendored copies. The conformance test splits
  its long checks into helpers and patches blob headers through `memcpy`
  instead of casting the byte buffer through `void *`. Each interop translation
  unit keeps the `NULL` macro inside one `NOLINT(modernize-use-nullptr)` block,
  because MSVC's C mode has no `nullptr`. No behaviour or ABI change.
