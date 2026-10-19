#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Regression test for scripts/bench/fetch-corpus.sh (BUG-022, HISS-02, #135).

A dead URL, a corrupt body and a stalled server must each fail the fetch, name
the failing clip in stderr, and leave nothing at the cached-download path; a
good body must still succeed (positive control, so the failures above cannot
come from a broken harness).

A generated clip (lavfi:gradients) must match its pinned sha256: a wrong pin,
a missing pin and a failed generation each fail, name the clip in stderr, and
leave no clip behind.

An extraction failure after a good download must remove the partial .yuv, name
the clip with EXTRACT FAILED: <name>, and exit 1.

--check-categories verifies category coverage against the lock file: an empty
category or an unknown clip exits 1; fully covered categories exit 0 with
nothing downloaded.
"""

from __future__ import annotations

import hashlib
import http.server
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = pathlib.Path(
    os.environ.get("FETCH_CORPUS_SCRIPT", ROOT / "scripts" / "bench" / "fetch-corpus.sh")
)
GOOD_BODY = b"pelorus corpus fixture\n"
GOOD_SHA = hashlib.sha256(GOOD_BODY).hexdigest()
HANG_SECONDS = 8
DEADLINE_SECONDS = 2


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/good.bin":
            self._send(200, GOOD_BODY)
        elif self.path == "/corrupt.bin":
            self._send(200, b"<html>not the clip</html>")
        elif self.path == "/hang.bin":
            time.sleep(HANG_SECONDS)
            self._send(200, GOOD_BODY)
        else:
            self._send(404, b"<html>404 Not Found</html>")

    def _send(self, code: int, body: bytes) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client gave up (deadline case); nothing to deliver

    def log_message(self, *_args: object) -> None:
        return


EMPTY_SHA = hashlib.sha256(b"").hexdigest()
STUB_OK = '#!/bin/sh\nfor a; do last="$a"; done\n: > "$last"\n'
STUB_FAIL = '#!/bin/sh\nfor a; do last="$a"; done\n: > "$last"\nexit 1\n'


# Every failure names the corpus entry as "<KIND>: <name> (<detail>)".
NAMED = ": clip ("


def run_fetch(
    port: int, name: str, sha: str, url: str = "", stub_text: str = STUB_OK
) -> tuple[int, float, list[str], str]:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        corpus = tmpdir / "corpus"
        lock = tmpdir / "corpus.lock"
        stub = tmpdir / "ffmpeg-stub"
        stub.write_text(stub_text)
        stub.chmod(0o755)
        url = url or f"http://127.0.0.1:{port}/{name}"
        lock.write_text(f"clip | {url} | {sha} | -ss 0 | 16x16 | yuv420p | 1 | 30\n")
        env = dict(
            os.environ,
            FFMPEG=str(stub),
            CORPUS=str(corpus),
            CORPUS_LOCK=str(lock),
            CURL_MAX_TIME=str(DEADLINE_SECONDS),
        )
        start = time.monotonic()
        proc = subprocess.run(
            ["bash", str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=HANG_SECONDS * 3,
            check=False,
        )
        elapsed = time.monotonic() - start
        leftovers = sorted(p.name for p in corpus.iterdir()) if corpus.exists() else []
        return proc.returncode, elapsed, leftovers, proc.stderr


def run_check_categories(
    categories_text: str,
    lock_text: str = "clip | http://127.0.0.1/clip.bin | 00 | -ss 0 | 16x16 | yuv420p | 1 | 30\n",
) -> tuple[int, str, str, list[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        corpus = tmpdir / "corpus"
        lock = tmpdir / "corpus.lock"
        categories = tmpdir / "corpus.categories"
        lock.write_text(lock_text)
        categories.write_text(categories_text)
        env = dict(
            os.environ,
            CORPUS=str(corpus),
            CORPUS_LOCK=str(lock),
            CORPUS_CATEGORIES=str(categories),
        )
        proc = subprocess.run(
            ["bash", str(SCRIPT), "--check-categories"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        leftovers = sorted(p.name for p in corpus.iterdir()) if corpus.exists() else []
        return proc.returncode, proc.stdout, proc.stderr, leftovers


def check_downloads(port: int, failures: list[str]) -> None:
    for name, sha, label in (
        ("missing.bin", GOOD_SHA, "404"),
        ("corrupt.bin", GOOD_SHA, "checksum mismatch"),
    ):
        rc, _elapsed, left, err = run_fetch(port, name, sha)
        if rc == 0:
            failures.append(f"{label}: fetch exited 0")
        if left:
            failures.append(f"{label}: files left behind: {left}")
        if NAMED not in err:
            failures.append(f"{label}: clip not named as ': clip (' in stderr: {err!r}")

    rc, elapsed, left, err = run_fetch(port, "hang.bin", GOOD_SHA)
    if rc == 0:
        failures.append("stalled server: fetch exited 0")
    if elapsed >= HANG_SECONDS:
        failures.append(f"stalled server: no deadline ({elapsed:.1f}s)")
    if left:
        failures.append(f"stalled server: files left behind: {left}")
    if NAMED not in err:
        failures.append(f"stalled server: clip not named as ': clip (' in stderr: {err!r}")

    rc, _elapsed, left, _err = run_fetch(port, "good.bin", GOOD_SHA)
    if rc != 0:
        failures.append(f"positive control: fetch exited {rc}")
    if "good.bin" not in left or "clip.yuv" not in left:
        failures.append(f"positive control: expected good.bin and clip.yuv, got {left}")

    # Extract failure: stub fails during extraction after a good download
    rc, _elapsed, left, err = run_fetch(port, "good.bin", GOOD_SHA, stub_text=STUB_FAIL)
    if rc != 1:
        failures.append(f"extract failure: expected exit 1, got {rc}")
    if "EXTRACT FAILED: clip" not in err:
        failures.append(f"extract failure: 'EXTRACT FAILED: clip' not in stderr: {err!r}")
    if "clip.yuv" in left:
        failures.append(f"extract failure: clip.yuv left behind: {left}")


def check_generated(port: int, failures: list[str]) -> None:
    # Generated clip: the stub writes an empty file, so EMPTY_SHA is the good pin.
    for sha, stub_text, label in (
        ("0" * 64, STUB_OK, "generated clip, wrong pin"),
        ("-", STUB_OK, "generated clip, no pin"),
        (EMPTY_SHA, STUB_FAIL, "generated clip, generation failed"),
    ):
        rc, _elapsed, left, err = run_fetch(port, "", sha, "lavfi:gradients", stub_text)
        if rc == 0:
            failures.append(f"{label}: fetch exited 0")
        if left:
            failures.append(f"{label}: files left behind: {left}")
        if NAMED not in err:
            failures.append(f"{label}: clip not named as ': clip (' in stderr: {err!r}")
    rc, _elapsed, left, _err = run_fetch(port, "", EMPTY_SHA, "lavfi:gradients")
    if rc != 0:
        failures.append(f"generated clip positive control: fetch exited {rc}")
    if left != ["clip.meta", "clip.yuv"]:
        failures.append(f"generated clip positive control: expected clip.meta and clip.yuv, got {left}")


def check_categories(failures: list[str]) -> None:
    # Category checks (--check-categories)
    # 1. Boundary: categories file with one empty category -> exits 1, output has MISSING <cat>
    rc, out, err, left = run_check_categories("cat1 | | #198, filter\n")
    combined = out + err
    if rc != 1:
        failures.append(f"check-categories empty category: expected exit 1, got {rc}")
    if "MISSING cat1" not in combined:
        failures.append(f"check-categories empty category: 'MISSING cat1' not in output: {combined!r}")
    if left:
        failures.append(f"check-categories empty category: files downloaded/left behind: {left}")

    # 2. Negative: category naming a clip absent from the lock -> exits 1
    rc, _out, err, left = run_check_categories("cat1 | absent_clip | #198, filter\n")
    if rc != 1:
        failures.append(f"check-categories absent clip: expected exit 1, got {rc}")
    if "UNKNOWN CLIP: absent_clip in category cat1" not in err:
        failures.append(f"check-categories absent clip: clip not named in stderr: {err!r}")
    if left:
        failures.append(f"check-categories absent clip: files downloaded/left behind: {left}")

    # 3. Positive: all categories covered -> exits 0, nothing downloaded
    rc, out, _err, left = run_check_categories(
        "cat1 | clip | #198, filter\ncat2 | clip | #200\n"
    )
    if rc != 0:
        failures.append(f"check-categories covered: expected exit 0, got {rc}")
    if "cat1: clip" not in out or "cat2: clip" not in out:
        failures.append(f"check-categories covered: expected categories not in output: {out!r}")
    if left:
        failures.append(f"check-categories covered: files downloaded/left behind: {left}")


def main() -> int:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    failures: list[str] = []
    check_downloads(port, failures)
    check_generated(port, failures)
    check_categories(failures)
    server.shutdown()
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if not failures:
        print("fetch-corpus.sh: 404, corrupt body, stalled server, extract failures, "
              "unpinned/mismatched generated clips, and category coverage all verified")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
