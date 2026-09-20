#!/usr/bin/env bash
# PostToolUse(Edit|Write) — clang-format C/H files in place after an edit.
# Quiet; never blocks (always exit 0).
set -euo pipefail

f="$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null || true)"
[ -z "$f" ] && exit 0
[ -f "$f" ] || exit 0

case "$f" in
    *.c|*.h|*.cpp|*.hpp|*.cc)
        # Skip the FFmpeg-tree sources — they follow FFmpeg's own style, not ours.
        case "$f" in */ffmpeg-patches/files/*) exit 0;; esac
        if command -v clang-format >/dev/null 2>&1; then
            clang-format -i "$f" 2>/dev/null || true
        fi
        ;;
esac
exit 0
