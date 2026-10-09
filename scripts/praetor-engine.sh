#!/bin/sh
#
# praetor-engine.sh - run the Praetor engine pinned at PRAETOR_REF.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
#   praetor-engine.sh <engine args...>   run the pinned engine with these args
#   praetor-engine.sh --print-path       print the pinned engine path, run nothing
#
# The pin is PRAETOR_REF in .github/workflows/standards-gate.yml, the commit CI
# installs. The engine lives in <repo>/.workingdir/bin/<PRAETOR_REF>/ (git
# ignored), installed there with `go install` on first use. A praetorctl or
# standardsctl found on PATH is never used: its commit moves without notice.
#
# Fail closed: a missing pin, a missing Go toolchain with an empty cache, a
# failed install, or an engine whose `version` does not name the pinned commit
# stops with one line on stderr and a nonzero status.
#
# PRAETORCTL=<path> overrides the choice. The override runs unchecked, and one
# stderr line names it so the run never passes for the pin.

set -eu

die() {
	printf 'praetor-engine: %s\n' "$*" >&2
	exit 1
}

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
gate="$root/.github/workflows/standards-gate.yml"

print_only=0
if [ "${1:-}" = "--print-path" ]; then
	print_only=1
	shift
fi

if [ -n "${PRAETORCTL:-}" ]; then
	# A bare name (PRAETORCTL=standardsctl) resolves on PATH, as make does.
	case "$PRAETORCTL" in
	*/*) ;;
	*)
		name=$PRAETORCTL
		PRAETORCTL=$(command -v -- "$name" 2>/dev/null) || die "PRAETORCTL=$name is not on PATH"
		;;
	esac
	[ -x "$PRAETORCTL" ] || die "PRAETORCTL=$PRAETORCTL is not an executable file"
	printf 'praetor-engine: PRAETORCTL override %s, pin not checked\n' "$PRAETORCTL" >&2
	if [ "$print_only" = 1 ]; then
		printf '%s\n' "$PRAETORCTL"
		exit 0
	fi
	exec "$PRAETORCTL" "$@"
fi

[ -f "$gate" ] || die "$gate not found; cannot read PRAETOR_REF"
pin=$(sed -n 's/^[[:space:]]*PRAETOR_REF:[[:space:]]*\([0-9a-f]\{40\}\)[[:space:]]*$/\1/p' "$gate")
case "$pin" in
'' | *'
'*) die "expected exactly one 40-hex PRAETOR_REF in .github/workflows/standards-gate.yml" ;;
esac
short=$(printf '%s' "$pin" | cut -c1-12)

dir="$root/.workingdir/bin/$pin"
bin="$dir/standardsctl"

# `version` prints "praetorctl version <12-hex build commit>"; compare the
# commit, not the whole line.
check_version() {
	found=$("$1" version 2>/dev/null | head -n 1) || found=''
	case "$found" in
	*"$short"*) return 0 ;;
	esac
	die "engine $1 reports '${found:-no version output}', expected commit $short (PRAETOR_REF $pin); remove $dir and rerun to reinstall, or set PRAETORCTL=<path>"
}

if [ ! -x "$bin" ]; then
	command -v go >/dev/null 2>&1 ||
		die "pinned engine $short is not installed and go is not on PATH; install Go (version in standards-gate.yml) and rerun, or set PRAETORCTL=<path>"
	tmp=$(mktemp -d "${TMPDIR:-/tmp}/praetor-engine.XXXXXX")
	trap 'rm -rf "$tmp"' EXIT
	printf 'praetor-engine: installing pinned engine %s into %s\n' "$short" "$dir" >&2
	GOBIN="$tmp" go install "github.com/cordanaLLM/praetor/cmd/standardsctl@$pin" >&2 ||
		die "go install of praetor@$short failed (offline or no module access?); retry online or set PRAETORCTL=<path>"
	check_version "$tmp/standardsctl"
	mkdir -p "$dir"
	# TMPDIR is often another filesystem (tmpfs), where mv copies in place and
	# a concurrent hook can run a half-written binary. Stage the copy next to
	# the target so the final mv is an atomic rename.
	stage=$(mktemp "$dir/.standardsctl.XXXXXX")
	trap 'rm -rf "$tmp"; rm -f "$stage"' EXIT
	cp "$tmp/standardsctl" "$stage"
	chmod 755 "$stage"
	mv -f "$stage" "$bin"
fi
check_version "$bin"

if [ "$print_only" = 1 ]; then
	printf '%s\n' "$bin"
	exit 0
fi
exec "$bin" "$@"
