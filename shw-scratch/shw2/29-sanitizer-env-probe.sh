#!/usr/bin/env bash
# shw-2 step 29: is the sanitizers-job failure in step 28 ("Linker clang does
# not support sanitizer arguments") a container-image gap or the branch?
# Runs the ci.yml sanitizers command on master (f03badd) and on HEAD in the
# same image, and lists the clang sanitizer runtimes present.
set -uo pipefail
clang --version | head -1
echo "asan runtimes:"; find / -name 'libclang_rt.asan*' 2>/dev/null | head -5; echo "(end)"
printf 'int main(void){return 0;}\n' > /tmp/t.c
clang -fsanitize=address,undefined /tmp/t.c -o /tmp/t 2>&1 | tail -3; echo "direct clang -fsanitize link rc=${PIPESTATUS[0]}"
for rev in f03badd HEAD; do
    rm -rf /tmp/san && git clone -q /w /tmp/san && git -C /tmp/san checkout -q "$rev" || exit 1
    (cd /tmp/san && CC=clang meson setup build-san -Db_sanitize=address,undefined -Db_lundef=false \
        -Dc_args=-fno-sanitize-recover=alignment >/tmp/san-$rev.log 2>&1)
    echo "$rev: meson setup rc=$? :: $(grep -E '^ERROR' /tmp/san-$rev.log | head -1)"
done
