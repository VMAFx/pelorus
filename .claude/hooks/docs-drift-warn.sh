#!/usr/bin/env bash
# PostToolUse(Edit|Write) — remind about the per-PR deliverables when a
# user-discoverable surface or the interop ABI changes (ADR-0100 / ADR-0108).
# Warns to stderr; never blocks.
set -euo pipefail

f="$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null || true)"
[ -z "$f" ] && exit 0

case "$f" in
    *include/pelorus/interop.h)
        echo "  [drift] interop ABI touched — it is APPEND-ONLY (R1/R2). Bump PELORUS_ABI_MINOR, extend libpelorus/test/interop_test.c, update docs/api/interop-abi.md + any consuming patch. (/bump-abi)" >&2 ;;
    *include/pelorus/*.h)
        echo "  [drift] public header touched — update docs/ for the surface (ADR-0100), add a changelog.d fragment, and re-check the ffmpeg-patches that consume it." >&2 ;;
    */ffmpeg-patches/files/*.c)
        echo "  [drift] filter source touched — regenerate the patch (/ffmpeg-build-patches), update docs/metrics/*, add a changelog.d fragment + docs/rebase-notes.md entry." >&2 ;;
    *meson_options.txt)
        echo "  [drift] build flag touched — document it (default + effect, ADR-0100) and add a changelog.d fragment." >&2 ;;
esac
exit 0
