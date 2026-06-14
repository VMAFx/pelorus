#!/usr/bin/env bash
# SessionStart — brief orientation. Quiet on the happy path; only actionable lines.
set -euo pipefail

root=$(git rev-parse --show-toplevel 2>/dev/null || exit 0)
cd "$root"

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
echo "Pelorus session — branch: $branch" >&2
echo "  read .workingdir/PLAN.md + STATE.md and AGENTS.md before non-trivial work." >&2

if [ "$branch" = "master" ]; then
    echo "  on master: feature work goes on a branch + PR (see CONTRIBUTING.md)." >&2
fi

# build/ staleness vs the meson files (clangd / test freshness)
if [ -d build ]; then
    for mf in meson.build libpelorus/meson.build; do
        if [ -f "$mf" ] && [ "$mf" -nt build/build.ninja ]; then
            echo "  WARN: $mf is newer than build/ — rerun 'ninja -C build'." >&2
            break
        fi
    done
fi
exit 0
