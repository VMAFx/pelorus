---
name: lint-all
description: Use when linting Pelorus before a push or PR — clang-format check + clang-tidy (Power-of-10/CERT profile) + glslang shader compile. The touched-file-clean gate.
---

# /lint-all

```sh
# format (no writes)
clang-format --dry-run --Werror $(find libpelorus -name '*.c' -o -name '*.h')

# static analysis — needs a configured build/ for compile_commands.json
meson setup build 2>/dev/null || true
clang-tidy -p build libpelorus/src/*.c

# shaders compile to SPIR-V (also covered by `meson test --suite=fast`)
for s in libpelorus/shaders/*.comp; do
    glslangValidator -V --target-env vulkan1.2 "$s" -o /dev/null
done
```

## Rules

- Every PR leaves **touched files** clean to clang-format + clang-tidy
  (AGENTS.md hard rule 6). A `// NOLINT` needs an inline citation (ADR / research
  digest / load-bearing invariant) — a bare NOLINT is itself a violation.
- The `.clang-tidy` profile maps to NASA Power-of-10 + SEI CERT C
  (docs/principles.md §1–§2). Refactor rather than suppress.
- FFmpeg-tree sources (`ffmpeg-patches/files/`) are linted by FFmpeg's own
  build, not this profile.
