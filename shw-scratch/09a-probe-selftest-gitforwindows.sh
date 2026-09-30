#!/usr/bin/env bash
# shw-1 step 9a (probe only, no file edits): does scripts/check-build-config.py
# --self-test pass natively when "git" is Git for Windows (native argv, no MSYS
# brace/glob expansion) instead of MSYS2's /usr/bin/git?
set -uo pipefail
cd /c/tmp/pel/shw
echo "-- MSYS git: $(command -v git)"
python3 scripts/check-build-config.py --self-test; echo "rc=$?"
export PATH="/c/Program Files/Git/cmd:$PATH"
echo "-- Git for Windows: $(command -v git) $(git --version)"
python3 scripts/check-build-config.py --self-test; echo "rc=$?"
