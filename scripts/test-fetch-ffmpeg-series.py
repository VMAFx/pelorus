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
    no output, even when `gh` would accept everything.
"""

from __future__ import annotations

import hashlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

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
            (tools / tool).chmod(0o755)
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


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pelorus-series-test-") as tmp:
        work = pathlib.Path(tmp)
        refused = missing_verifier(work)
        if refused:
            failures.append(refused)
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
        print(f"fetch-ffmpeg-series: {len(refusals()) + 2} planted defects refused "
              "(unpack checks, existing output, missing cosign), control accepted")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
