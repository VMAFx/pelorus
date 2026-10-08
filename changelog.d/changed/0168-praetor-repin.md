- Re-pinned the Praetor governance engine from `0af07a73` to `492a00f9`. Every
  hosted `meson setup` now passes `--werror`, because the engine's audit reads
  only the command line. clang-tidy now lints the libpelorus tests, the
  `pelorus_qp_report` tool, and the two Meson-built FFmpeg-patch tests as well
  as the library, from one list in `.config/clang-tidy/lane-files.txt`; the 13
  units that build only inside an FFmpeg tree carry dated exceptions until
  #94 adds a lane for them. A local `make audit` no longer fails on the
  declined Git hooks, and the documentation gate needs Node.js 22.12 or newer
  ([ADR-0168](docs/adr/0168-praetor-engine-492a00f.md)).
