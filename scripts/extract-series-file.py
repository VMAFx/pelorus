#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Write one file that the shared FFmpeg fix series creates, read from its patches.

The SEI fit helper (ffmpeg-patches/files/pelorus_sei_fit.h) includes
libavcodec/nvenc_sei.h, which only the shared FFmpeg fix series creates
(ADR-0218). The fast suite builds against that file without a copy in this
repository: Meson's ffmpeg_series_dir option names the directory that
scripts/fetch-ffmpeg-series.sh printed, and this script writes the file from
the one patch that creates it.

It refuses (exit 1), never guesses, when:
  - series.txt is missing, empty, or names a patch that is missing or not of
    the form NNNN-slug.patch;
  - no patch, or more than one patch, touches the path: a later edit would
    make the created text stale;
  - the patch does not create the path (no "new file mode", no /dev/null
    source) or holds anything but one hunk "@@ -0,0 +1,N @@" of exactly N
    added lines ending in a newline.

--plant NAME rewrites one known line after extraction, for the fast suite's
planted divergence test (ADR-0218): the line must occur exactly once.

Exit status: 0 written; 1 refused; 2 usage error.

    python3 -I scripts/extract-series-file.py SERIES_DIR PATH OUT [--plant NAME]
    python3 -I scripts/extract-series-file.py --self-test
"""

import os
import re
import sys
import tempfile
from pathlib import Path

MAX_PATCHES = 500
MAX_PATCH_LINES = 200000
MAX_FILE_LINES = 5000
PATCH_NAME = re.compile(r"^[0-9]{4}-[A-Za-z0-9._-]+\.patch$")
TARGET = re.compile(r"^[A-Za-z0-9_/.-]+$")
HUNK = re.compile(r"^@@ -0,0 \+1,([0-9]+) @@")
# One line each, rewritten so the series header no longer matches the frozen
# fit rules that sei_fit_test.c checks.
PLANTS = {
    "reserve": ("#define NVENC_HEVC_SEI_RESERVE   256\n",
                "#define NVENC_HEVC_SEI_RESERVE   255\n"),
}


class Refused(Exception):
    pass


def series_patches(series_dir):
    """Return the patch paths in series.txt order."""
    listing = series_dir / "series.txt"
    try:
        lines = listing.read_text(encoding="utf-8").splitlines()
    except OSError as err:
        raise Refused("cannot read %s: %s" % (listing, err)) from err
    patches = []
    for line in lines[:MAX_PATCHES + 64]:
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        if not PATCH_NAME.match(name):
            raise Refused("series.txt names %r, not NNNN-slug.patch" % name)
        if len(patches) == MAX_PATCHES:
            raise Refused("series.txt lists more than %d patches" % MAX_PATCHES)
        patch = series_dir / "patches" / name
        if not patch.is_file():
            raise Refused("series.txt names a missing patch: %s" % name)
        patches.append(patch)
    if not patches:
        raise Refused("series.txt lists no patch")
    return patches


def patch_lines(patch):
    with patch.open(encoding="utf-8", newline="") as handle:
        lines = handle.read().splitlines(keepends=True)
    if len(lines) > MAX_PATCH_LINES:
        raise Refused("%s has more than %d lines" % (patch.name, MAX_PATCH_LINES))
    return lines


def touches(line, target):
    if not line.startswith("diff --git "):
        return False
    return ("a/%s " % target) in line or line.rstrip("\n").endswith(" b/" + target)


def file_block(lines, target):
    """Return the lines of the diff block for target, or None."""
    start = next((i for i, line in enumerate(lines) if touches(line, target)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("diff --git ")), len(lines))
    return lines[start:end]


def created_text(block, target, name):
    """Return the text of the one hunk that creates target."""
    heads = [i for i, line in enumerate(block) if line.startswith("@@")]
    header = block[:heads[0]] if heads else block
    if "new file mode 100644\n" not in header or "--- /dev/null\n" not in header \
            or "+++ b/%s\n" % target not in header:
        raise Refused("%s does not create %s" % (name, target))
    if len(heads) != 1:
        raise Refused("%s creates %s in %d hunks, want 1" % (name, target, len(heads)))
    match = HUNK.match(block[heads[0]])
    count = int(match.group(1)) if match else -1
    if not 0 < count <= MAX_FILE_LINES:
        raise Refused("%s: hunk header %r is not @@ -0,0 +1,N @@"
                      % (name, block[heads[0]].rstrip("\n")))
    body = block[heads[0] + 1:heads[0] + 1 + count]
    if len(body) != count or any(not line.startswith("+") or not line.endswith("\n")
                                 for line in body):
        raise Refused("%s: hunk of %s holds other than %d added lines"
                      % (name, target, count))
    rest = block[heads[0] + 1 + count:]
    if rest and rest[0] != "-- \n" and rest[0][:1] in ("+", "-", " ", "\\"):
        raise Refused("%s: hunk of %s runs past %d lines or ends without a newline"
                      % (name, target, count))
    return "".join(line[1:] for line in body)


def extract(series_dir, target, plant=None):
    if not TARGET.match(target) or ".." in target.split("/"):
        raise Refused("path %r is not a plain relative path" % target)
    found = []
    for patch in series_patches(series_dir):
        block = file_block(patch_lines(patch), target)
        if block is not None:
            found.append((patch.name, block))
    if len(found) != 1:
        raise Refused("%d series patches touch %s, want exactly 1: %s"
                      % (len(found), target, ", ".join(n for n, _ in found) or "none"))
    text = created_text(found[0][1], target, found[0][0])
    if plant is not None:
        old, new = PLANTS[plant]
        if text.count(old) != 1:
            raise Refused("plant %s: %r occurs %d times, want 1"
                          % (plant, old.rstrip("\n"), text.count(old)))
        text = text.replace(old, new)
    return text


def write(out, text):
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, out)


def run(series_dir, target, out, plant=None):
    try:
        if not series_dir.is_absolute():
            raise Refused("SERIES_DIR must be absolute: %s" % series_dir)
        write(out, extract(series_dir, target, plant))
    except (Refused, OSError, UnicodeDecodeError) as err:
        print("extract-series-file: refused: %s" % err, file=sys.stderr)
        return 1
    return 0


def new_file_patch(target, body, count=None, mode="new file mode 100644\n"):
    lines = body.splitlines(keepends=True)
    return ("From 0 Mon Sep 17 00:00:00 2001\nSubject: [PATCH] x\n---\n"
            "diff --git a/%s b/%s\n%sindex 0000000..1111111\n--- /dev/null\n"
            "+++ b/%s\n@@ -0,0 +1,%d @@\n%s-- \n2.51.0\n"
            % (target, target, mode, target,
               len(lines) if count is None else count, "".join("+" + x for x in lines)))


def edit_patch(target):
    return ("From 0 Mon Sep 17 00:00:00 2001\nSubject: [PATCH] y\n---\n"
            "diff --git a/%s b/%s\nindex 1111111..2222222 100644\n--- a/%s\n"
            "+++ b/%s\n@@ -1,1 +1,1 @@\n-a\n+b\n-- \n2.51.0\n"
            % (target, target, target, target))


T = "libavcodec/nvenc_sei.h"
GOOD = "/* x */\n#define NVENC_HEVC_SEI_RESERVE   256\nint y;\n"
SELF_TEST_CASES = [
    # (label, {patch name: text} in series order, plant, expected text or None)
    ("creates the file", {"0001-a.patch": new_file_patch(T, GOOD)}, None, GOOD),
    ("plant rewrites one line", {"0001-a.patch": new_file_patch(T, GOOD)}, "reserve",
     GOOD.replace("256", "255")),
    ("plant line absent", {"0001-a.patch": new_file_patch(T, "int y;\n")}, "reserve", None),
    ("no patch creates it", {"0001-a.patch": new_file_patch("x.h", GOOD)}, None, None),
    ("a later patch edits it", {"0001-a.patch": new_file_patch(T, GOOD),
                                "0002-b.patch": edit_patch(T)}, None, None),
    ("edit only, not created", {"0001-b.patch": edit_patch(T)}, None, None),
    ("hunk count too large", {"0001-a.patch": new_file_patch(T, GOOD, count=4)}, None, None),
    ("hunk count too small", {"0001-a.patch": new_file_patch(T, GOOD, count=2)}, None, None),
    ("no new file mode", {"0001-a.patch": new_file_patch(T, GOOD, mode="")}, None, None),
    ("no trailing newline", {"0001-a.patch": new_file_patch(T, GOOD).replace(
        "+int y;\n", "+int y;\n\\ No newline at end of file\n")}, None, None),
    ("bad series name", {"a.patch": new_file_patch(T, GOOD)}, None, None),
]


def self_case(root, patches, plant):
    series = root / "series"
    (series / "patches").mkdir(parents=True)
    (series / "series.txt").write_text(
        "# order\n" + "".join(n + "\n" for n in patches), encoding="utf-8")
    for name, text in patches.items():
        (series / "patches" / name).write_text(text, encoding="utf-8", newline="")
    out = root / "out.h"
    status = run(series, T, out, plant)
    return out.read_text(encoding="utf-8") if status == 0 else None


def self_test():
    """Plant each defect and require a refusal; the good cases must match."""
    failures = 0
    for label, patches, plant, want in SELF_TEST_CASES:
        with tempfile.TemporaryDirectory() as tmp:
            got = self_case(Path(tmp), patches, plant)
        if got != want:
            print("self-test FAIL: %s: got %r, want %r" % (label, got, want))
            failures += 1
    with tempfile.TemporaryDirectory() as tmp:
        if run(Path("relative"), T, Path(tmp) / "out.h") != 1:
            print("self-test FAIL: relative SERIES_DIR is not refused")
            failures += 1
    print("self-test: %d cases, %d failures" % (len(SELF_TEST_CASES) + 1, failures))
    return 1 if failures else 0


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    plant = None
    if len(argv) == 5 and argv[3] == "--plant" and argv[4] in PLANTS:
        plant = argv[4]
    elif len(argv) != 3:
        print((__doc__ or "").strip().splitlines()[-2].strip(), file=sys.stderr)
        return 2
    return run(Path(argv[0]), argv[1], Path(argv[2]), plant)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
