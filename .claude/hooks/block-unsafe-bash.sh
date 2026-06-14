#!/usr/bin/env bash
# PreToolUse(Bash) guard — block irreversibly destructive commands.
# Exit 2 blocks the call and shows the message to the model. Quiet otherwise.
set -euo pipefail

cmd="$(python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || true)"
[ -z "$cmd" ] && exit 0

block() { echo "BLOCKED by .claude/hooks/block-unsafe-bash.sh: $1" >&2; exit 2; }

case "$cmd" in
    *"rm -rf /"*|*"rm -rf /*"*|*"rm -fr /"*)        block "refusing 'rm -rf /' — too destructive";;
    *"git push"*"--force"*master*|*"git push"*"-f"*master*) block "no force-push to master (ADR/branch-protection)";;
    *"git push"*"--force"*main*|*"git push"*"-f"*main*)     block "no force-push to main";;
    *"git reset --hard"*origin/master*)              block "refusing hard reset onto origin/master";;
    *"git clean -"*x*f*|*"git clean -"*f*x*)         block "git clean -xf wipes ignored build trees — run manually if intended";;
    *":(){ :|:& };:"*)                               block "fork bomb";;
esac
exit 0
