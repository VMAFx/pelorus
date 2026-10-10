#!/usr/bin/env bash
#
# fetch-ffmpeg-series.sh — download, verify and unpack the shared FFmpeg fix
# series that Pelorus applies before its own patch stack (ADR-0185).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# The series is a release of FFMPEG_SERIES_REPO (VMAFx/ffmpeg-patches) pinned
# in root build-config.env by tag, commit and tarball sha256. Usage:
#
#   scripts/fetch-ffmpeg-series.sh OUT_DIR
#       Download the release files into the new directory OUT_DIR, verify them
#       and unpack the tarball into OUT_DIR/series (base.env, series.txt,
#       patches/). Prints OUT_DIR/series on stdout.
#   scripts/fetch-ffmpeg-series.sh --unpack TARBALL OUT_DIR
#       For a tarball that a full run already verified (the tester image build
#       gets it from the runner): check its sha256 pin only, then unpack it as
#       above.
#   scripts/fetch-ffmpeg-series.sh --self-test
#       Verify the genuine release, then refuse a tarball with one byte
#       flipped (with the old pin and with its own sha256 pinned), a wrong
#       sha256 pin, a tampered SHA256SUMS and a wrong commit pin, each at the
#       check meant to catch it.
#
# Every check is required and fails closed; on failure OUT_DIR is removed and
# the script exits 1:
#   1. the tarball's sha256 equals FFMPEG_SERIES_SHA256;
#   2. `cosign verify-blob` of SHA256SUMS: the certificate names the series
#      repository's release-build.yml at refs/tags/FFMPEG_SERIES_TAG, issued
#      by GitHub Actions OIDC;
#   3. `sha256sum --check --strict SHA256SUMS`, read only once its signature
#      holds, over every file it lists, the tarball among them;
#   4. `gh attestation verify` of the tarball: SLSA provenance signed by that
#      workflow on a GitHub-hosted runner, built from refs/tags/<tag> at
#      FFMPEG_SERIES_COMMIT;
#   5. the unpacked base.env names the FFmpeg remote, tag and commit of
#      build-config.env, and every patch series.txt lists is present.
#
# Env:
#   FFMPEG_SERIES_FROM  directory that already holds the four release files;
#                       they are copied and verified exactly like a download
#   GH_TOKEN            gh credential for check 4 (CI: github.token)
#   CURL_MAX_TIME       per-file download deadline in seconds (default 120)
set -euo pipefail
# A failing command inside $(...) must fail the assignment that reads it.
shopt -s inherit_errexit

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=build-config.env
source "$ROOT/build-config.env"
: "${FFMPEG_SERIES_REPO:?build-config.env has no FFMPEG_SERIES_REPO}"
: "${FFMPEG_SERIES_TAG:?build-config.env has no FFMPEG_SERIES_TAG}"
: "${FFMPEG_SERIES_COMMIT:?build-config.env has no FFMPEG_SERIES_COMMIT}"
: "${FFMPEG_SERIES_SHA256:?build-config.env has no FFMPEG_SERIES_SHA256}"
CURL_MAX_TIME="${CURL_MAX_TIME:-120}"

SERIES_NAME="ffmpeg-patches-${FFMPEG_SERIES_TAG}"
TARBALL="${SERIES_NAME}.tar.gz"
RELEASE_FILES=("$TARBALL" "${SERIES_NAME}.spdx.json" SHA256SUMS SHA256SUMS.sigstore.json)
RELEASE_URL="https://github.com/${FFMPEG_SERIES_REPO}/releases/download/${FFMPEG_SERIES_TAG}"
SIGNER_WORKFLOW="github.com/${FFMPEG_SERIES_REPO}/.github/workflows/release-build.yml"
OIDC_ISSUER="https://token.actions.githubusercontent.com"
OWNED_DIR=""

fail() {
    echo "fetch-ffmpeg-series: $*" >&2
    exit 1
}

cleanup() {
    local status=$?
    trap - EXIT
    if [[ $status -ne 0 && -n "$OWNED_DIR" ]]; then
        rm -rf -- "$OWNED_DIR"
    fi
    exit "$status"
}

# Run a verifier quietly; on failure name the check and show its last lines.
run_check() {
    local label="$1" log="$2"
    shift 2
    if ! "$@" >"$log" 2>&1; then
        tail -n 5 "$log" >&2
        echo "fetch-ffmpeg-series: $label failed" >&2
        return 1
    fi
}

# new_dir PATH: PATH must not exist and its parent must; prints it absolute.
new_dir() {
    local parent
    [[ -n "$1" ]] || fail "empty output directory"
    [[ ! -e "$1" && ! -L "$1" ]] || fail "refusing existing output directory: $1"
    parent="$(cd -- "$(dirname -- "$1")" 2>/dev/null && pwd -P)" ||
        fail "output parent is not an accessible directory: $1"
    mkdir -- "$parent/$(basename -- "$1")" || fail "cannot create output directory: $1"
    printf '%s/%s\n' "$parent" "$(basename -- "$1")"
}

download() {
    local dir="$1" name
    for name in "${RELEASE_FILES[@]}"; do
        if [[ -n "${FFMPEG_SERIES_FROM:-}" ]]; then
            cp -- "$FFMPEG_SERIES_FROM/$name" "$dir/$name" ||
                fail "FFMPEG_SERIES_FROM has no $name"
        elif ! curl --fail --silent --show-error --location \
            --proto '=https' --proto-redir '=https' \
            --max-time "$CURL_MAX_TIME" --retry 2 --output "$dir/$name" \
            -- "$RELEASE_URL/$name"; then
            fail "download failed: $RELEASE_URL/$name"
        fi
    done
}

# check_pin TARBALL SHA256: check 1.
check_pin() {
    local actual
    actual="$(sha256sum -- "$1" | cut -d ' ' -f 1)"
    if [[ "$actual" != "$2" ]]; then
        echo "fetch-ffmpeg-series: $(basename -- "$1") sha256 $actual, pinned $2" >&2
        return 1
    fi
}

# check_sums DIR: check 3, in DIR so the listed names resolve.
check_sums() {
    (cd -- "$1" && sha256sum --check --strict SHA256SUMS)
}

# verify DIR SHA256 COMMIT: checks 1 to 4 on the release files in DIR.
verify() {
    local dir="$1" sha="$2" commit="$3" tool
    for tool in sha256sum cosign gh; do
        command -v "$tool" >/dev/null 2>&1 || {
            echo "fetch-ffmpeg-series: $tool is required and not installed" >&2
            return 1
        }
    done
    check_pin "$dir/$TARBALL" "$sha" || return 1
    run_check "cosign verify-blob SHA256SUMS" "$dir/cosign.log" \
        cosign verify-blob --bundle "$dir/SHA256SUMS.sigstore.json" \
        --certificate-identity "https://${SIGNER_WORKFLOW}@refs/tags/${FFMPEG_SERIES_TAG}" \
        --certificate-oidc-issuer "$OIDC_ISSUER" "$dir/SHA256SUMS" || return 1
    grep -Eqx "[0-9a-f]{64}  ${TARBALL//./\\.}" "$dir/SHA256SUMS" || {
        echo "fetch-ffmpeg-series: SHA256SUMS does not list $TARBALL" >&2
        return 1
    }
    run_check "sha256sum --check SHA256SUMS" "$dir/sha256sum.log" check_sums "$dir" ||
        return 1
    run_check "gh attestation verify $TARBALL" "$dir/attestation.log" \
        gh attestation verify "$dir/$TARBALL" --repo "$FFMPEG_SERIES_REPO" \
        --signer-workflow "$SIGNER_WORKFLOW" \
        --source-ref "refs/tags/${FFMPEG_SERIES_TAG}" --source-digest "$commit" \
        --cert-oidc-issuer "$OIDC_ISSUER" --deny-self-hosted-runners || return 1
}

# base_value FILE KEY: the value of every KEY=value line, without running
# FILE; a key given twice yields two lines and so equals no pinned value.
base_value() {
    sed -n "s/^$2=\\([^[:space:]]*\\)\$/\\1/p" "$1"
}

# unpack TARBALL SERIES_DIR: check 5, after the members are known to stay in
# one top-level directory and to be plain files and directories.
unpack() {
    local tarball="$1" out="$2" key patch count=0 listing names
    # The listings are read whole, then matched: a reader that stops at its
    # first match would end tar early, and pipefail would hide the match.
    listing="$(tar -tvzf "$tarball")" || fail "cannot list $TARBALL"
    names="$(tar -tzf "$tarball")" || fail "cannot list $TARBALL"
    if grep -Evq '^[-d]' <<<"$listing"; then
        fail "$TARBALL holds a member that is no file or directory"
    fi
    if grep -Evxq "${SERIES_NAME//./\\.}/([A-Za-z0-9._-]+/)*[A-Za-z0-9._-]*" <<<"$names"; then
        fail "$TARBALL holds a member outside ${SERIES_NAME}/"
    fi
    if grep -Eq '(^|/)\.\.(/|$)' <<<"$names"; then
        fail "$TARBALL holds a parent-directory member"
    fi
    mkdir -- "$out"
    tar -xzf "$tarball" -C "$out" --strip-components=1 --no-same-owner --no-same-permissions
    for key in FFMPEG_REMOTE FFMPEG_TAG FFMPEG_COMMIT; do
        [[ "$(base_value "$out/base.env" "$key")" == "${!key}" ]] ||
            fail "series base.env $key differs from build-config.env"
    done
    while IFS= read -r patch || [[ -n "$patch" ]]; do
        case "$patch" in '' | '#'*) continue ;; esac
        [[ "$patch" =~ ^[0-9]{4}-[A-Za-z0-9._-]+\.patch$ && -f "$out/patches/$patch" ]] ||
            fail "series.txt lists a missing or malformed patch: $patch"
        count=$((count + 1))
    done <"$out/series.txt"
    ((count > 0)) || fail "series.txt lists no patch"
}

self_test_case() {
    local name="$1" expect="$2" dir="$3" sha="$4" commit="$5" log="$6"
    if verify "$dir" "$sha" "$commit" 2>"$log"; then
        fail "self-test: $name was accepted"
    fi
    grep -q -- "$expect" "$log" || fail "self-test: $name failed elsewhere: $(tail -n 1 "$log")"
    echo "self-test: $name refused ($expect)"
}

self_test() {
    local work wrong_sha wrong_commit flipped_sha byte
    work="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-series-selftest.XXXXXX")"
    OWNED_DIR="$work"
    mkdir "$work/good"
    download "$work/good"
    verify "$work/good" "$FFMPEG_SERIES_SHA256" "$FFMPEG_SERIES_COMMIT" ||
        fail "self-test: the genuine release was refused"
    unpack "$work/good/$TARBALL" "$work/good-series"
    echo "self-test: genuine release verified and unpacked"

    cp -r "$work/good" "$work/flipped"
    byte="$(od -An -tu1 -j 4096 -N 1 "$work/flipped/$TARBALL" | tr -d ' ')"
    printf '%b' "\\0$(printf '%03o' $(((byte + 1) % 256)))" |
        dd of="$work/flipped/$TARBALL" bs=1 seek=4096 conv=notrunc status=none
    self_test_case "tarball with one byte flipped" "pinned $FFMPEG_SERIES_SHA256" \
        "$work/flipped" "$FFMPEG_SERIES_SHA256" "$FFMPEG_SERIES_COMMIT" "$work/flipped.log"

    # A pin moved to the tampered tarball: the signed checksums still refuse it.
    flipped_sha="$(sha256sum -- "$work/flipped/$TARBALL" | cut -d ' ' -f 1)"
    self_test_case "tampered tarball with its own sha256 pinned" \
        "sha256sum --check SHA256SUMS failed" "$work/flipped" "$flipped_sha" \
        "$FFMPEG_SERIES_COMMIT" "$work/flipped-pinned.log"

    wrong_sha="$(printf '%s' "$FFMPEG_SERIES_SHA256" | tr '0-9a-f' '1-9a-f0')"
    self_test_case "wrong sha256 pin" "pinned $wrong_sha" "$work/good" \
        "$wrong_sha" "$FFMPEG_SERIES_COMMIT" "$work/pin.log"

    # Checksums that still match their files but are not the signed ones.
    cp -r "$work/good" "$work/sums"
    printf '%s  extra-file\n' "$FFMPEG_SERIES_SHA256" >>"$work/sums/SHA256SUMS"
    cp -- "$work/sums/$TARBALL" "$work/sums/extra-file"
    self_test_case "tampered SHA256SUMS" "cosign verify-blob SHA256SUMS failed" \
        "$work/sums" "$FFMPEG_SERIES_SHA256" "$FFMPEG_SERIES_COMMIT" "$work/sums.log"

    wrong_commit="$(printf '%s' "$FFMPEG_SERIES_COMMIT" | tr '0-9a-f' '1-9a-f0')"
    self_test_case "wrong commit pin" "gh attestation verify $TARBALL failed" \
        "$work/good" "$FFMPEG_SERIES_SHA256" "$wrong_commit" "$work/commit.log"

    rm -rf -- "$work"
    OWNED_DIR=""
    echo "self-test: OK"
}

trap cleanup EXIT
case "${1:-}" in
    --self-test)
        [[ $# -eq 1 ]] || fail "usage: $0 --self-test"
        self_test
        ;;
    --unpack)
        [[ $# -eq 3 ]] || fail "usage: $0 --unpack TARBALL OUT_DIR"
        [[ -f "$2" ]] || fail "no such tarball: $2"
        check_pin "$2" "$FFMPEG_SERIES_SHA256" || exit 1
        OWNED_DIR="$(new_dir "$3")"
        unpack "$2" "$OWNED_DIR/series"
        printf '%s\n' "$OWNED_DIR/series"
        ;;
    -* | '')
        fail "usage: $0 OUT_DIR | --unpack TARBALL OUT_DIR | --self-test"
        ;;
    *)
        [[ $# -eq 1 ]] || fail "usage: $0 OUT_DIR"
        OWNED_DIR="$(new_dir "$1")"
        download "$OWNED_DIR"
        verify "$OWNED_DIR" "$FFMPEG_SERIES_SHA256" "$FFMPEG_SERIES_COMMIT" || exit 1
        unpack "$OWNED_DIR/$TARBALL" "$OWNED_DIR/series"
        echo "fetch-ffmpeg-series: ${FFMPEG_SERIES_REPO} ${FFMPEG_SERIES_TAG} verified" >&2
        printf '%s\n' "$OWNED_DIR/series"
        ;;
esac
