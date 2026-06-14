---
name: format-all
description: Use when formatting Pelorus C sources — run clang-format in place over libpelorus to the project .clang-format (K&R, 4-space, 100 cols). FFmpeg-tree sources are excluded.
---

# /format-all

```sh
clang-format -i $(find libpelorus -name '*.c' -o -name '*.h')
```

To check without writing (the CI gate form):

```sh
clang-format --dry-run --Werror $(find libpelorus -name '*.c' -o -name '*.h')
```

## Notes

- `ffmpeg-patches/files/*.c` are **excluded** — they live in the FFmpeg tree and
  follow FFmpeg's own style, not the project `.clang-format`.
- The `auto-format-on-edit` hook already formats single files on save; this skill
  is for a whole-tree sweep.
