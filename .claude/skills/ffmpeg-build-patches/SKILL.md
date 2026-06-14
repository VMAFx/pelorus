---
name: ffmpeg-build-patches
description: Use when regenerating the FFmpeg patch stack after editing a filter source under ffmpeg-patches/files/ — runs generate.sh to rebuild the 000N-*.patch artifacts in an isolated worktree.
---

# /ffmpeg-build-patches

`ffmpeg-patches/files/*.c` is the source of truth; the `000N-*.patch` files are
generated. After editing a filter source (or adding a new one), regenerate:

```sh
cd ffmpeg-patches
FFMPEG_REPO=/home/kilian/dev/ffmpeg-8 BASE_TAG=n8.1.1 ./generate.sh
```

`generate.sh` checks out a pristine base in an **isolated git worktree** (never
touches your main FFmpeg checkout), drops in each filter's source *inside its own
iteration*, wires the registration hunks (configure / Makefile / allfilters.c),
commits one-per-filter, and `git format-patch`es the whole cumulative range.

## Adding a new filter to the stack

1. Add `ffmpeg-patches/files/vf_pelorus_<name>_vulkan.c`.
2. Add `ffmpeg-patches/.commit-msg-<name>.txt` (Conventional Commit + `ADR:` trailer).
3. Add the `<name>` registration block to the python step in `generate.sh`
   (extern in allfilters.c, OBJS in Makefile, `_filter_deps` + `require_pkg_config
   libpelorus` in configure) and append `<name>` to the `for filter in …` loop.
4. Add the patch filename to `series.txt`.
5. Run `generate.sh`, then verify with `/ffmpeg-apply-patches`.

## Rules

- Commit the edited source **and** the regenerated patch in the same PR
  (AGENTS.md hard rule 5); add a `docs/rebase-notes.md` entry.
- The stack is cumulative — later patches' context depends on earlier ones.
