#!/usr/bin/env bash
#
# check-ffmpeg-licence-self-test.sh - plants each defect in a stub ffmpeg and
# requires check-ffmpeg-licence.sh to refuse it; the unmodified stub must pass.
# A gate that is never seen failing is not a gate.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
set -euo pipefail

here="$(cd -- "$(dirname -- "$0")" && pwd -P)"
gate="${here}/check-ffmpeg-licence.sh"
work="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-licence-gate.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT

good_config='--prefix=/usr --enable-gpl --enable-version3 --enable-vulkan --enable-libx264'
good_licence='ffmpeg is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation; either version 3 of the License, or (at your option) any later version.'

# make_stub NAME CONFIGURE_LINE LICENCE_TEXT -> path of a stub that answers -version and -L
make_stub() {
  local path="${work}/$1"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'case " $* " in\n'
    printf "  *' -L '*) printf '%%s\\\\n' '%s' ;;\n" "$3"
    printf "  *) printf 'ffmpeg version test\\\\nconfiguration: %%s\\\\n' '%s' ;;\n" "$2"
    printf 'esac\n'
  } >"$path"
  chmod +x "$path"
  printf '%s\n' "$path"
}

failures=0
expect() { # expect accept|refuse NAME STUB
  local rc=0
  "$gate" "$3" >/dev/null 2>&1 || rc=$?
  if [ "$1" = accept ] && [ "$rc" -ne 0 ]; then
    echo "licence gate self-test: $2 was refused (rc=${rc})" >&2
    failures=$((failures + 1))
  elif [ "$1" = refuse ] && [ "$rc" -ne 1 ]; then
    echo "licence gate self-test: $2 was not refused (rc=${rc})" >&2
    failures=$((failures + 1))
  fi
}

expect accept "GPLv3 build" "$(make_stub good "$good_config" "$good_licence")"
for banned in nonfree cuda-nvcc cuda-sdk libfdk-aac libfdk_aac decklink libmpeghdec; do
  expect refuse "planted --enable-${banned}" \
    "$(make_stub "plant-${banned}" "${good_config} --enable-${banned}" "$good_licence")"
done
expect refuse "LGPL build without --enable-gpl" \
  "$(make_stub no-gpl '--prefix=/usr --enable-version3' "$good_licence")"
expect refuse "GPL build without --enable-version3" \
  "$(make_stub no-v3 '--prefix=/usr --enable-gpl' "$good_licence")"
expect refuse "nonfree text in -L" \
  "$(make_stub text-nonfree "$good_config" 'This version has nonfree parts compiled in. either version 3 of the License')"
expect refuse "-L naming the LGPL version 3" \
  "$(make_stub lgpl3 "$good_config" 'ffmpeg is free software; you can redistribute it and/or modify it under the terms of the GNU Lesser General Public License as published by the Free Software Foundation; either version 3 of the License, or (at your option) any later version.')"
expect refuse "-L without GPL version 3" \
  "$(make_stub gpl2 "$good_config" 'either version 2 of the License')"
expect refuse "several banned flags at once" \
  "$(make_stub plant-prefix "${good_config} --enable-nonfree=yes --enable-decklink" "$good_licence")"
expect accept "similar but different flags" \
  "$(make_stub similar "${good_config} --enable-cuda-llvm --enable-libx265" "$good_licence")"
printf '#!/usr/bin/env bash\nexit 3\n' >"${work}/broken" && chmod +x "${work}/broken"
expect refuse "binary that fails -version" "${work}/broken"
rc=0
"$gate" "${work}/missing" >/dev/null 2>&1 || rc=$?
if [ "$rc" -ne 2 ]; then
  echo "licence gate self-test: a missing binary was not a usage error (rc=${rc})" >&2
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  exit 1
fi
echo "licence gate self-test: every planted defect refused"
