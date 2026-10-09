#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Regression test for scripts/bench/fetch-corpus.sh (BUG-022, HISS-02).

A dead URL, a corrupt body and a stalled server must each fail the fetch and
leave nothing at the cached-download path; a good body must still succeed
(positive control, so the failures above cannot come from a broken harness).
A generated clip (lavfi:gradients) must match its pinned sha256: a wrong pin,
a missing pin and a failed generation each fail and leave no clip behind.
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


def run_fetch(
    port: int, name: str, sha: str, url: str = "", stub_text: str = STUB_OK
) -> tuple[int, float, list[str]]:
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
        return proc.returncode, elapsed, leftovers


def main() -> int:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    failures: list[str] = []

    for name, sha, label in (
        ("missing.bin", GOOD_SHA, "404"),
        ("corrupt.bin", GOOD_SHA, "checksum mismatch"),
    ):
        rc, _elapsed, left = run_fetch(port, name, sha)
        if rc == 0:
            failures.append(f"{label}: fetch exited 0")
        if left:
            failures.append(f"{label}: files left behind: {left}")

    rc, elapsed, left = run_fetch(port, "hang.bin", GOOD_SHA)
    if rc == 0:
        failures.append("stalled server: fetch exited 0")
    if elapsed >= HANG_SECONDS:
        failures.append(f"stalled server: no deadline ({elapsed:.1f}s)")
    if left:
        failures.append(f"stalled server: files left behind: {left}")

    rc, _elapsed, left = run_fetch(port, "good.bin", GOOD_SHA)
    if rc != 0:
        failures.append(f"positive control: fetch exited {rc}")
    if "good.bin" not in left or "clip.yuv" not in left:
        failures.append(f"positive control: expected good.bin and clip.yuv, got {left}")

    # Generated clip: the stub writes an empty file, so EMPTY_SHA is the good pin.
    for sha, stub_text, label in (
        ("0" * 64, STUB_OK, "generated clip, wrong pin"),
        ("-", STUB_OK, "generated clip, no pin"),
        (EMPTY_SHA, STUB_FAIL, "generated clip, generation failed"),
    ):
        rc, _elapsed, left = run_fetch(port, "", sha, "lavfi:gradients", stub_text)
        if rc == 0:
            failures.append(f"{label}: fetch exited 0")
        if left:
            failures.append(f"{label}: files left behind: {left}")
    rc, _elapsed, left = run_fetch(port, "", EMPTY_SHA, "lavfi:gradients")
    if rc != 0:
        failures.append(f"generated clip positive control: fetch exited {rc}")
    if left != ["clip.meta", "clip.yuv"]:
        failures.append(f"generated clip positive control: expected clip.meta and clip.yuv, got {left}")

    server.shutdown()
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if not failures:
        print("fetch-corpus.sh: 404, corrupt body, stalled server and unpinned or "
              "mismatched generated clips all fail closed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
