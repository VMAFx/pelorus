#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Offline regression test for scripts/fetch-ffmpeg-series.sh (ADR-0185).

The network checks (signed checksums, build provenance) have their planted
defects in the script's own --self-test, which CI runs. This test covers what
needs no network: the sha256 pin and the unpack checks of the --unpack mode.
Each case builds a small series tarball, pins its sha256 in a throwaway
build-config.env next to a copy of the script, and expects:

  - a well-formed tarball to unpack (positive control);
  - a tarball that is not the pinned one, a truncated one, a series made for
    another FFmpeg commit or tag, a base.env that names two commits, a listed
    patch that is missing or leaves patches/, an empty series, a member outside
    the top-level directory, a parent-directory member and a symlink member to
    be refused, with no output directory left behind;
  - an existing output directory to be refused and left as it is;
  - a full run on a host without cosign to stop at the missing verifier, with
    no output, even when `gh` would accept everything;
  - a stalled download to abort within stall timeout and retry before the retry
    window expires.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = pathlib.Path(
    os.environ.get("FETCH_SERIES_SCRIPT", ROOT / "scripts" / "fetch-ffmpeg-series.sh")
)
TAG = "v9.9.9-rc.1"
TOP = f"ffmpeg-patches-{TAG}"
FFMPEG = {
    "FFMPEG_REMOTE": "https://github.com/FFmpeg/FFmpeg.git",
    "FFMPEG_TAG": "n1.2.3",
    "FFMPEG_COMMIT": "a" * 40,
}
PATCH = "0001-fixture.patch"


def base_env(values: dict[str, str]) -> bytes:
    return "".join(f"{key}={value}\n" for key, value in values.items()).encode()


def tarball(members: dict[str, bytes], links: dict[str, str] | None = None) -> bytes:
    """A gzip tarball of regular files (and symlinks), in insertion order."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)
    return buffer.getvalue()


def series(base: dict[str, str] | None = None, listed: str = PATCH + "\n", **extra: bytes) -> dict[str, bytes]:
    members = {
        f"{TOP}/base.env": base_env(base or FFMPEG),
        f"{TOP}/series.txt": b"# fixture series\n" + listed.encode(),
        f"{TOP}/patches/{PATCH}": b"fixture patch\n",
    }
    members.update(extra)
    return members


def run_case(work: pathlib.Path, name: str, body: bytes, pinned: bytes | None = None,
             existing: bool = False) -> tuple[int, str, pathlib.Path]:
    """Unpack `body` with the sha256 of `pinned` (default: of `body`) as the pin."""
    root = work / name
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "fetch-ffmpeg-series.sh")
    pin = hashlib.sha256(body if pinned is None else pinned).hexdigest()
    config = dict(FFMPEG, FFMPEG_SERIES_REPO="VMAFx/ffmpeg-patches", FFMPEG_SERIES_TAG=TAG,
                  FFMPEG_SERIES_COMMIT="b" * 40, FFMPEG_SERIES_SHA256=pin)
    (root / "build-config.env").write_bytes(base_env(config))
    archive = root / f"{TOP}.tar.gz"
    archive.write_bytes(body)
    out = root / "out"
    if existing:
        out.mkdir()
        (out / "keep").write_text("mine\n")
    result = subprocess.run(
        ["bash", str(root / "scripts" / "fetch-ffmpeg-series.sh"), "--unpack", str(archive), str(out)],
        check=False, capture_output=True, text=True,
    )
    return result.returncode, result.stderr, out


def refusals() -> dict[str, tuple[bytes, bytes | None, str]]:
    """name -> (tarball, tarball whose sha256 is pinned or None for itself, expected stderr)."""
    good = tarball(series())
    return {
        "not the pinned tarball": (good, good + b"\0", "pinned "),
        "series for another FFmpeg commit": (
            tarball(series(base=dict(FFMPEG, FFMPEG_COMMIT="c" * 40))), None,
            "base.env FFMPEG_COMMIT differs",
        ),
        "series for another FFmpeg tag": (
            tarball(series(base=dict(FFMPEG, FFMPEG_TAG="n1.2.4"))), None,
            "base.env FFMPEG_TAG differs",
        ),
        "base.env names two commits": (
            tarball({**series(), f"{TOP}/base.env": base_env(FFMPEG) + b"FFMPEG_COMMIT=" + b"c" * 40 + b"\n"}),
            None, "base.env FFMPEG_COMMIT differs",
        ),
        "truncated tarball": (good[: len(good) // 2], None, "cannot list"),
        "listed patch missing": (
            tarball(series(listed=PATCH + "\n0002-absent.patch\n")), None,
            "missing or malformed patch: 0002-absent.patch",
        ),
        "patch path leaves patches/": (
            tarball(series(listed="../base.env\n")), None, "missing or malformed patch",
        ),
        "empty series": (tarball(series(listed="")), None, "series.txt lists no patch"),
        "member outside the top-level directory": (
            tarball(series(**{"elsewhere/file": b"x\n"})), None, f"member outside {TOP}/",
        ),
        "parent-directory member": (
            tarball(series(**{f"{TOP}/../escape": b"x\n"})), None, "parent-directory member",
        ),
        "symlink member": (
            tarball(series(), links={f"{TOP}/link": "/etc/passwd"}), None,
            "member that is no file or directory",
        ),
    }


VERIFY_NEEDS = ("bash", "dirname", "basename", "sha256sum", "cut", "cp", "mkdir", "rm", "tail", "gh")


def missing_verifier(work: pathlib.Path) -> str:
    """A full run without cosign on PATH must stop before it trusts anything.

    FFMPEG_SERIES_FROM supplies four placeholder release files, so no network
    is used; `gh` on PATH is a stub that would accept everything. Returns a
    failure text, or "" when the run was refused and left no output.
    """
    root = work / "no-cosign"
    tools = root / "bin"
    release = root / "release"
    (root / "scripts").mkdir(parents=True)
    tools.mkdir()
    release.mkdir()
    shutil.copy(SCRIPT, root / "scripts" / "fetch-ffmpeg-series.sh")
    body = tarball(series())
    config = dict(FFMPEG, FFMPEG_SERIES_REPO="VMAFx/ffmpeg-patches", FFMPEG_SERIES_TAG=TAG,
                  FFMPEG_SERIES_COMMIT="b" * 40, FFMPEG_SERIES_SHA256=hashlib.sha256(body).hexdigest())
    (root / "build-config.env").write_bytes(base_env(config))
    for name in (f"{TOP}.tar.gz", f"{TOP}.spdx.json", "SHA256SUMS", "SHA256SUMS.sigstore.json"):
        (release / name).write_bytes(body if name.endswith(".tar.gz") else b"{}\n")
    for tool in VERIFY_NEEDS:
        found = shutil.which(tool)
        if tool == "gh" or found is None:
            (tools / tool).write_text("#!/bin/sh\nexit 0\n")
            (tools / tool).chmod(0o500)
        else:
            (tools / tool).symlink_to(found)
    out = root / "out"
    result = subprocess.run(
        [str(tools / "bash"), str(root / "scripts" / "fetch-ffmpeg-series.sh"), str(out)],
        check=False, capture_output=True, text=True,
        env={"PATH": str(tools), "FFMPEG_SERIES_FROM": str(release)},
    )
    if result.returncode == 0:
        return "no cosign on PATH: the release was accepted"
    if "cosign is required and not installed" not in result.stderr:
        return f"no cosign on PATH: refused for another reason: {result.stderr.strip()!r}"
    if out.exists():
        return "no cosign on PATH: output directory left behind"
    return ""


# The server thread polls accept() every 0.5 s; 240 polls bound it at 120 s,
# past the two 30 s script runs it serves.
ACCEPT_POLLS = 240


def _run_fetch_script(
    script: pathlib.Path, out: pathlib.Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run a copy of fetch-ffmpeg-series.sh into `out` with `env`, bounded at 30 s."""
    return subprocess.run(  # nosec B603 - fixed bash argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant bash command, no shell, explicit deadline
        ["bash", str(script), str(out)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
        env=env,
    )


def _write_curl_shim(
    tools: pathlib.Path, log_file: pathlib.Path, real_curl: str, port: int
) -> None:
    """Write a `curl` shim into `tools` that logs each call to `log_file` and sends its URL to the local server.

    With TEST_CURL_SHIM_CAPTURE=1 the shim only logs the call and exits 28.
    """
    shim_code = f"""#!{sys.executable} -I
import json
import os
import sys

log_file = {repr(str(log_file))}
real_curl = {repr(real_curl)}
target_port = {port}

with open(log_file, "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv) + "\\n")

if os.environ.get("TEST_CURL_SHIM_CAPTURE") == "1":
    sys.exit(28)

argv = [real_curl]
i = 1
while i < len(sys.argv):
    tok = sys.argv[i]
    if tok in ("--proto", "--proto-redir") and i + 1 < len(sys.argv):
        i += 2
        continue
    if tok == "--" and i + 1 < len(sys.argv):
        argv.append(tok)
        url = sys.argv[i + 1]
        name = url.rsplit("/", 1)[-1]
        argv.append(f"http://127.0.0.1:{{target_port}}/{{name}}")
        i += 2
        continue
    argv.append(tok)
    i += 1

os.execv(real_curl, argv)
"""
    shim = tools / "curl"
    shim.write_text(shim_code, encoding="utf-8")
    shim.chmod(0o500)


def _option_value(curl_words: list[str], name: str) -> str | None:
    """Value after the last `name` in a recorded curl call; the script passes option and value as two words."""
    if name not in curl_words:
        return None
    index = len(curl_words) - 1 - curl_words[::-1].index(name)
    return curl_words[index + 1] if index + 1 < len(curl_words) else None


def _check_captured_argv(log_file: pathlib.Path) -> str:
    """The first logged curl argv must carry --speed-time and a window wider than --max-time."""
    if not log_file.is_file():
        return "capture-only: no curl invocation logged"
    lines = log_file.read_text(encoding="utf-8").splitlines()
    if not lines:
        return "capture-only: empty curl log"
    curl_words = json.loads(lines[0])
    if "--speed-time" not in curl_words:
        return "capture-only: --speed-time not in curl argv"
    max_time = _option_value(curl_words, "--max-time") or ""
    window = _option_value(curl_words, "--retry-max-time") or ""
    if not (max_time.isdigit() and window.isdigit()):
        return f"capture-only: max-time {max_time!r}, retry window {window!r} not both integers"
    if int(window) <= int(max_time):
        return f"capture-only: retry window {window} <= max-time {max_time}"
    return ""


def _setup_stalled_env(work: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Lay out a script copy, a shim directory and a pinned build-config.env; return (root, tools, script)."""
    root = work / "stalled-download"
    tools = root / "bin"
    tools.mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    script_copy = root / "scripts" / "fetch-ffmpeg-series.sh"
    shutil.copy(SCRIPT, script_copy)
    config = dict(FFMPEG, FFMPEG_SERIES_REPO="VMAFx/ffmpeg-patches", FFMPEG_SERIES_TAG=TAG,
                  FFMPEG_SERIES_COMMIT="b" * 40, FFMPEG_SERIES_SHA256="0" * 64)
    (root / "build-config.env").write_bytes(base_env(config))
    return root, tools, script_copy


def _check_stalled_run(
    script: pathlib.Path, out: pathlib.Path, tools: pathlib.Path, connections: list[float]
) -> str:
    """Run fetch script under stall conditions and verify retry timing."""
    env = {
        **os.environ,
        "PATH": f"{tools}:{os.environ.get('PATH', '')}",
        "CURL_MAX_TIME": "6",
        "CURL_STALL_TIME": "1",
        "CURL_RETRY_WINDOW": "3",
    }
    res = _run_fetch_script(script, out, env)
    if res.returncode == 0:
        return "stalled download: expected failure, got exit 0"
    if "download failed" not in res.stderr:
        return f"stalled download: 'download failed' not in stderr: {res.stderr.strip()!r}"
    if len(connections) < 2:
        return f"stalled download: expected >= 2 connections, got {len(connections)}"
    delta = connections[1] - connections[0]
    if delta >= 6.0:
        return f"stalled download: second connection at {delta:.2f}s >= 6.0s (not aborted by stall detector)"
    if out.exists():
        return "stalled download: output directory left behind"
    return ""


def _check_capture_run(
    script: pathlib.Path, out: pathlib.Path, tools: pathlib.Path, log_file: pathlib.Path
) -> str:
    """Run fetch script in capture-only mode and verify default retry options."""
    env = {
        **os.environ,
        "PATH": f"{tools}:{os.environ.get('PATH', '')}",
        "CURL_MAX_TIME": "6",
        "TEST_CURL_SHIM_CAPTURE": "1",
    }
    _run_fetch_script(script, out, env)
    return _check_captured_argv(log_file)


def stalled_download(work: pathlib.Path) -> str:
    """A hung transfer must abort within stall timeout and retry before window ends."""
    real_curl = shutil.which("curl")
    if not real_curl:
        print("fetch-ffmpeg-series: stalled-download case skipped: curl not on PATH", file=sys.stderr)
        return ""

    root, tools, script_copy = _setup_stalled_env(work)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(16)
    server_sock.settimeout(0.5)
    port = server_sock.getsockname()[1]
    connections: list[float] = []
    client_socks: list[socket.socket] = []
    stop_event = threading.Event()

    def _accept_loop() -> None:
        """Accept connections and never answer; record each start time, at most ACCEPT_POLLS polls."""
        for _ in range(ACCEPT_POLLS):
            if stop_event.is_set():
                return
            try:
                conn, _ = server_sock.accept()
            except (TimeoutError, OSError):
                continue
            connections.append(time.monotonic())
            client_socks.append(conn)

    server_thread = threading.Thread(target=_accept_loop)
    server_thread.daemon = True
    server_thread.start()

    log_file = root / "curl-calls.jsonl"
    _write_curl_shim(tools, log_file, real_curl, port)

    try:
        err = _check_stalled_run(script_copy, root / "out1", tools, connections)
        if err:
            return err
        log_file.unlink(missing_ok=True)
        return _check_capture_run(script_copy, root / "out2", tools, log_file)
    finally:
        stop_event.set()
        server_thread.join(timeout=2.0)
        for client in client_socks:
            client.close()
        server_sock.close()


def main() -> int:
    """Run the control, every planted defect and the stalled-download case; exit 1 on any failure."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pelorus-series-test-") as tmp:
        work = pathlib.Path(tmp)
        # Each fixture-free case returns its failure text, empty on a pass.
        failures.extend(filter(None, (missing_verifier(work), stalled_download(work))))
        code, stderr, out = run_case(work, "good", tarball(series()))
        if code != 0 or not (out / "series" / "patches" / PATCH).is_file():
            failures.append(f"positive control: exit {code}, stderr {stderr.strip()!r}")
        for index, (name, (body, pinned, expected)) in enumerate(refusals().items()):
            code, stderr, out = run_case(work, f"case{index}", body, pinned)
            if code == 0:
                failures.append(f"{name}: accepted")
            elif expected not in stderr:
                failures.append(f"{name}: refused for another reason: {stderr.strip()!r}")
            elif out.exists():
                failures.append(f"{name}: output directory left behind")
        code, stderr, out = run_case(work, "existing", tarball(series()), existing=True)
        if code == 0 or "refusing existing output directory" not in stderr:
            failures.append(f"existing output directory: exit {code}, stderr {stderr.strip()!r}")
        elif (out / "keep").read_text() != "mine\n":
            failures.append("existing output directory: its content was changed")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if not failures:
        print(f"fetch-ffmpeg-series: {len(refusals()) + 3} planted defects refused "
              "(unpack checks, existing output, missing cosign, stalled download), control accepted")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
