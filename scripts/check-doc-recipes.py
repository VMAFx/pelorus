#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Fail when a documented ffmpeg recipe breaks the zero-copy contract.

Scope: every fenced code block in README.md and docs/**/*.md that holds an
``ffmpeg`` command. A command is the ``ffmpeg`` line plus its continuation lines
(a trailing backslash or an open double quote). Rules:

  hw-hwdownload   a recipe whose video encoder is a hardware encoder (*_nvenc,
                  *_qsv, *_vaapi, *_vulkan, *_amf, ...) must not contain
                  ``hwdownload``: the frame would round-trip through system
                  memory (research 0172, hop matrix).
  sw-label        a recipe whose video encoder is a software encoder may use
                  ``hwdownload`` only when a ``#`` comment in the same code
                  block says it is ``inherent``.
  unknown-encoder a recipe with ``hwdownload`` and a video encoder that is in
                  neither list is refused, so a new encoder cannot slip past
                  the two rules above.
  retained-table  the table after the line ``<!-- gate: retained-frames -->``
                  lists, per filter, the frames it keeps (default and
                  maximum); both numbers must equal the ones computed from
                  libpelorus/include/pelorus/denoise.h and the filter sources
                  under ffmpeg-patches/files/.
  extra-hw-frames a recipe that chains ``pelorus_mc_vulkan`` or
                  ``pelorus_denoise_vulkan`` behind a hardware decoder
                  (``-hwaccel``) must set ``-extra_hw_frames N`` with N at
                  least the frames its filters keep.

Exit 0 clean, 1 findings, 2 the gate could not run (missing source, unreadable
constant).

    python3 -I scripts/check-doc-recipes.py [--root D] [--disable RULE]
    python3 -I scripts/check-doc-recipes.py --self-test
"""

import re
import sys
import tempfile
from pathlib import Path

RULES = ("hw-hwdownload", "sw-label", "unknown-encoder", "retained-table", "extra-hw-frames")

HW_SUFFIXES = ("_nvenc", "_qsv", "_vaapi", "_vulkan", "_amf", "_videotoolbox", "_mf",
               "_v4l2m2m", "_omx", "_rkmpp", "_cuvid")
SW_ENCODERS = {
    "libx264", "libx264rgb", "libx265", "libaom-av1", "libsvtav1", "librav1e",
    "libvpx", "libvpx-vp9", "libvvenc", "libxvid", "libopenh264", "libkvazaar",
    "ffv1", "rawvideo", "mpeg4", "prores_ks", "png", "mjpeg", "utvideo", "huffyuv",
}
DENOISE_HEADER = "libpelorus/include/pelorus/denoise.h"
DENOISE_SRC = "ffmpeg-patches/files/vf_pelorus_denoise_vulkan.c"
MC_SRC = "ffmpeg-patches/files/vf_pelorus_mc_vulkan.c"
TABLE_MARK = "<!-- gate: retained-frames -->"

ENCODER_RE = re.compile(r"(?:^|\s)-(?:c:v(?::\d+)?|codec:v(?::\d+)?|vcodec)\s+(\S+)")
FENCE_RE = re.compile(r"^\s*```")


class GateError(Exception):
    """The gate could not evaluate its input (exit 2)."""


def classify(encoder):
    if encoder.endswith(HW_SUFFIXES):
        return "hw"
    if encoder in SW_ENCODERS:
        return "sw"
    return "unknown"


def strip_comment(line):
    """Drop a trailing shell comment that is outside double quotes."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == "#" and not quoted and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def is_ffmpeg_start(line):
    words = strip_comment(line).split()
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        words.pop(0)
    return bool(words) and words[0] == "ffmpeg"


def split_blocks(text):
    """Yield (comment_text, [(first_line_no, command_text, [line_nos])]) per fence."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not FENCE_RE.match(lines[i]):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not FENCE_RE.match(lines[j]):
            j += 1
        yield parse_block(lines, i + 1, j)
        i = j + 1


def parse_block(lines, start, end):
    comments = []
    commands = []
    k = start
    while k < end:
        raw = lines[k]
        if raw.lstrip().startswith("#"):
            comments.append(raw.lstrip()[1:])
            k += 1
            continue
        if "#" in raw and not raw.lstrip().startswith("#"):
            tail = raw[len(strip_comment(raw)):]
            if tail.strip().startswith("#"):
                comments.append(tail.strip()[1:])
        if not is_ffmpeg_start(raw):
            k += 1
            continue
        parts = []
        nos = []
        while k < end:
            cur = strip_comment(lines[k])
            nos.append(k + 1)
            joined = " ".join(parts + [cur])
            cont = cur.rstrip().endswith("\\")
            parts.append(cur.rstrip().rstrip("\\"))
            k += 1
            if not cont and joined.count('"') % 2 == 0:
                break
        commands.append((nos[0], " ".join(parts), nos))
    return " ".join(comments), commands


def find_line(nos, lines_text, needle):
    """Line number inside a command that holds ``needle`` (first), else the first."""
    for no in nos:
        if needle in lines_text[no - 1]:
            return no
    return nos[0]


def filter_options(command, name):
    """Option dict of the first ``name=...`` filter instance in ``command``."""
    m = re.search(re.escape(name) + r"(?:=([^,\s\"\[;]*))?", command)
    if not m:
        return None
    opts = {}
    for item in (m.group(1) or "").split(":"):
        if "=" in item:
            key, val = item.split("=", 1)
            opts[key] = val
    return opts


def int_opt(opts, key, default):
    try:
        return int(opts.get(key, default))
    except ValueError:
        return default


def required_frames(command, consts):
    """Frames the Pelorus filters in ``command`` keep, or 0 when none is used."""
    need = 0
    if filter_options(command, "pelorus_mc_vulkan") is not None:
        need += consts["mc"]
    opts = filter_options(command, "pelorus_denoise_vulkan")
    if opts is not None:
        need += int_opt(opts, "prev", consts["denoise_prev_default"])
        need += int_opt(opts, "lookahead", consts["denoise_lookahead_default"])
    return need


def check_recipes(path, text, consts, off):
    findings = []
    lines = text.splitlines()
    for comments, commands in split_blocks(text):
        for first, command, nos in commands:
            encoders = ENCODER_RE.findall(command)
            kinds = {classify(e) for e in encoders}
            has_dl = re.search(r"\bhwdownload\b", command) is not None
            dl_line = find_line(nos, lines, "hwdownload")
            if has_dl and "hw" in kinds and "hw-hwdownload" not in off:
                findings.append((path, dl_line, "hw-hwdownload",
                                 "hardware encoder %s with hwdownload: full frames cross system memory"
                                 % ", ".join(e for e in encoders if classify(e) == "hw")))
            if has_dl and "sw" in kinds and "inherent" not in comments.lower() \
                    and "sw-label" not in off:
                findings.append((path, dl_line, "sw-label",
                                 "software encoder %s uses hwdownload without a '# ... inherent' comment in the block"
                                 % ", ".join(e for e in encoders if classify(e) == "sw")))
            if has_dl and "unknown" in kinds and "unknown-encoder" not in off:
                findings.append((path, dl_line, "unknown-encoder",
                                 "encoder %s is neither hardware nor software in the gate's lists"
                                 % ", ".join(e for e in encoders if classify(e) == "unknown")))
            need = required_frames(command, consts)
            m = re.search(r"-extra_hw_frames\s+(\d+)", command)
            hw_dec = re.search(r"-hwaccel\s+(?!none\b)\S+", command) is not None
            if need and (hw_dec or m) and "extra-hw-frames" not in off:
                if not m:
                    findings.append((path, first, "extra-hw-frames",
                                     "hardware decode with filters that keep %d frame(s) and no -extra_hw_frames" % need))
                elif int(m.group(1)) < need:
                    findings.append((path, find_line(nos, lines, "-extra_hw_frames"), "extra-hw-frames",
                                     "-extra_hw_frames %s is below the %d frame(s) the filters keep"
                                     % (m.group(1), need)))
    return findings


def check_table(path, text, consts, off):
    if "retained-table" in off or TABLE_MARK not in text:
        return []
    findings = []
    lines = text.splitlines()
    start = next(i for i, x in enumerate(lines) if TABLE_MARK in x)
    expect = {
        "pelorus_mc_vulkan": (consts["mc"], consts["mc"]),
        "pelorus_denoise_vulkan": (
            consts["denoise_prev_default"] + consts["denoise_lookahead_default"],
            consts["denoise_prev_max"] + consts["denoise_lookahead_max"]),
    }
    seen = set()
    for i in range(start + 1, len(lines)):
        row = lines[i].strip()
        if not row.startswith("|"):
            if seen:
                break
            continue
        cells = [c.strip() for c in row.strip("|").split("|")]
        m = re.match(r"^`(pelorus_\w+)`$", cells[0])
        if not m or m.group(1) not in expect:
            continue
        seen.add(m.group(1))
        try:
            got = (int(cells[-2]), int(cells[-1]))
        except ValueError:
            findings.append((path, i + 1, "retained-table", "last two cells of the %s row are not integers" % m.group(1)))
            continue
        if got != expect[m.group(1)]:
            findings.append((path, i + 1, "retained-table",
                             "%s keeps (default, maximum) %s in the source, the table says %s"
                             % (m.group(1), expect[m.group(1)], got)))
    for name in sorted(set(expect) - seen):
        findings.append((path, start + 1, "retained-table", "no row for %s" % name))
    return findings


def read(root, rel):
    try:
        return (root / rel).read_text(encoding="utf-8")
    except OSError as exc:
        raise GateError("cannot read %s: %s" % (rel, exc))


def option_row(src, name, rel):
    """Return (default, max_token) of the AVOption ``name`` in ``src``."""
    m = re.search(r'\{\s*"%s",.*?\{\.i64\s*=\s*(\d+)\}\s*,\s*(\w+)\s*,\s*(\w+)\s*,' % re.escape(name),
                  src, re.S)
    if not m:
        raise GateError("cannot find the AVOption %r in %s" % (name, rel))
    return int(m.group(1)), m.group(3)


def load_constants(root):
    header = read(root, DENOISE_HEADER)
    denoise = read(root, DENOISE_SRC)
    mc = re.sub(r"/\*.*?\*/|//[^\n]*", "", read(root, MC_SRC), flags=re.S)
    m = re.search(r"#define\s+PEL_DENOISE_MAX_PREV\s+(\d+)", header)
    if not m:
        raise GateError("cannot find PEL_DENOISE_MAX_PREV in %s" % DENOISE_HEADER)
    max_prev = int(m.group(1))
    prev_def, prev_max = option_row(denoise, "prev", DENOISE_SRC)
    look_def, look_max = option_row(denoise, "lookahead", DENOISE_SRC)
    if prev_max != "PEL_DENOISE_MAX_PREV":
        raise GateError("the prev option maximum is %s, not PEL_DENOISE_MAX_PREV" % prev_max)
    try:
        look_max = int(look_max)
    except ValueError:
        raise GateError("the lookahead option maximum %r is not a number" % look_max)
    # mc keeps one cloned previous frame; any ring or second clone field changes it.
    if len(re.findall(r"^\s*AVFrame \*prev;", mc, re.M)) != 1 or re.search(r"\bring\b", mc) \
            or len(re.findall(r"\bav_frame_clone\(", mc)) != 1:
        raise GateError("%s no longer keeps exactly one cloned previous frame; update this gate" % MC_SRC)
    return {
        "mc": 1,
        "denoise_prev_default": prev_def,
        "denoise_prev_max": max_prev,
        "denoise_lookahead_default": look_def,
        "denoise_lookahead_max": look_max,
    }


def doc_files(root):
    out = []
    readme = root / "README.md"
    if readme.is_file():
        out.append(readme)
    out.extend(sorted((root / "docs").rglob("*.md")))
    return out


def run(root, off):
    consts = load_constants(root)
    findings = []
    for path in doc_files(root):
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8")
        findings += check_recipes(rel, text, consts, off)
        findings += check_table(rel, text, consts, off)
    return findings


def report(findings):
    for path, line, rule, msg in findings:
        print("%s:%d: [%s] %s" % (path, line, rule, msg))
    print("check-doc-recipes: %d finding(s)" % len(findings))


# --- self-test --------------------------------------------------------------

FAKE_HEADER = "#define PEL_DENOISE_MAX_PREV 4\n"
FAKE_DENOISE = ('{"prev", "d", OFFSET(n_prev), AV_OPT_TYPE_INT, {.i64 = 3}, 0, PEL_DENOISE_MAX_PREV, FLAGS},\n'
                '{"lookahead", "d", OFFSET(lookahead), AV_OPT_TYPE_INT, {.i64 = 0}, 0, 1, FLAGS},\n')
FAKE_MC = "    AVFrame *prev;\n    /* a 1-deep ring */\n    clone = av_frame_clone(in);\n"


def fence(*body):
    return "```bash\n" + "\n".join(body) + "\n```\n"


def case_table(mc, dn_def, dn_max):
    return (TABLE_MARK + "\n\n| Filter | Default | Maximum |\n| --- | --- | --- |\n"
            "| `pelorus_mc_vulkan` | %d | %d |\n| `pelorus_denoise_vulkan` | %d | %d |\n"
            % (mc, mc, dn_def, dn_max))


# (name, document text, expected (line, rule) or None for a clean document)
def self_cases():
    nv = "ffmpeg -i in.mkv \\\n  -vf \"hwupload,pelorus_deband_vulkan,hwdownload,format=p010le\" \\\n  -c:v hevc_nvenc out.mkv"
    return [
        ("hardware encoder with hwdownload", "# t\n\n" + fence(nv), (5, "hw-hwdownload")),
        ("hardware encoder, one-line quoted continuation",
         fence('ffmpeg -vf "hwupload,', '  pelorus_deband_vulkan,hwdownload" -c:v h264_qsv o.mkv'),
         (3, "hw-hwdownload")),
        ("hardware encoder behind an env assignment",
         fence('LIBVA_DRIVER_NAME=iHD ffmpeg -vf "hwdownload" -c:v hevc_vaapi o.mkv'),
         (2, "hw-hwdownload")),
        ("software encoder, hwdownload unlabelled",
         fence('ffmpeg -vf "hwdownload,format=yuv420p" -c:v libsvtav1 o.mkv'), (2, "sw-label")),
        ("software encoder, hwdownload labelled inherent",
         fence("# hwdownload is inherent: libsvtav1 takes system-memory frames",
               'ffmpeg -vf "hwdownload,format=yuv420p" -c:v libsvtav1 o.mkv'), None),
        ("hardware encoder without hwdownload",
         fence('ffmpeg -vf "pelorus_deband_vulkan,hwmap=derive_device=vaapi" -c:v h264_vaapi o.mkv'), None),
        ("unclassified encoder with hwdownload",
         fence('ffmpeg -vf "hwdownload" -c:v mystery_enc o.mkv'), (2, "unknown-encoder")),
        ("retained table disagrees with the source (mc)", case_table(2, 3, 5), (5, "retained-table")),
        ("retained table disagrees with the source (denoise maximum)", case_table(1, 3, 4),
         (6, "retained-table")),
        ("retained table matches the source", case_table(1, 3, 5), None),
        ("hardware decode, mc, no -extra_hw_frames",
         fence('ffmpeg -hwaccel cuda -i in.mkv -vf "pelorus_mc_vulkan" -c:v hevc_nvenc o.mkv'),
         (2, "extra-hw-frames")),
        ("-extra_hw_frames below denoise prev + lookahead",
         fence('ffmpeg -hwaccel vaapi -extra_hw_frames 3 -i in.mkv '
               '-vf "pelorus_denoise_vulkan=prev=4:lookahead=1" -c:v hevc_vaapi o.mkv'),
         (2, "extra-hw-frames")),
        ("-extra_hw_frames covers mc + denoise",
         fence('ffmpeg -hwaccel vaapi -extra_hw_frames 5 -i in.mkv '
               '-vf "pelorus_mc_vulkan,pelorus_denoise_vulkan=prev=3" -c:v hevc_vaapi o.mkv'), None),
    ]


def self_test():
    failures = 0
    consts = {
        "mc": 1, "denoise_prev_default": 3, "denoise_prev_max": 4,
        "denoise_lookahead_default": 0, "denoise_lookahead_max": 1,
    }
    for name, text, expect in self_cases():
        got = check_recipes("t.md", text, consts, ()) + check_table("t.md", text, consts, ())
        pairs = [(line, rule) for _, line, rule, _ in got]
        if expect is None:
            ok = not got
        else:
            ok = pairs == [expect]
        if not ok:
            failures += 1
            print("self-test FAIL: %s: expected %s, got %s" % (name, expect, pairs))
            continue
        if expect is not None:
            # Proven check: with the rule switched off the same defect passes.
            off = (expect[1],)
            quiet = check_recipes("t.md", text, consts, off) + check_table("t.md", text, consts, off)
            if quiet:
                failures += 1
                print("self-test FAIL: %s: still reported with %s disabled" % (name, expect[1]))
    failures += self_test_constants()
    if failures:
        print("check-doc-recipes self-test: %d failure(s)" % failures)
        return 1
    print("check-doc-recipes self-test: ok")
    return 0


def self_test_constants():
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, body in ((DENOISE_HEADER, FAKE_HEADER), (DENOISE_SRC, FAKE_DENOISE), (MC_SRC, FAKE_MC)):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(body, encoding="utf-8")
        try:
            got = load_constants(root)
        except GateError as exc:
            print("self-test FAIL: constants from a well-formed tree: %s" % exc)
            return 1
        if got != {"mc": 1, "denoise_prev_default": 3, "denoise_prev_max": 4,
                   "denoise_lookahead_default": 0, "denoise_lookahead_max": 1}:
            failures += 1
            print("self-test FAIL: constants parsed as %s" % got)
        for label, rel, body in (
                ("a ring added to mc", MC_SRC, FAKE_MC + "AVFrame *ring[2];\n"),
                ("a missing lookahead option", DENOISE_SRC, FAKE_DENOISE.splitlines()[0] + "\n"),
                ("a missing header constant", DENOISE_HEADER, "\n")):
            saved = (root / rel).read_text(encoding="utf-8")
            (root / rel).write_text(body, encoding="utf-8")
            try:
                load_constants(root)
                failures += 1
                print("self-test FAIL: %s was not refused" % label)
            except GateError:
                pass
            (root / rel).write_text(saved, encoding="utf-8")
        (root / MC_SRC).unlink()
        try:
            load_constants(root)
            failures += 1
            print("self-test FAIL: a missing source was not refused")
        except GateError:
            pass
    return failures


def main(argv):
    root = Path(".")
    off = []
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--self-test":
            return self_test()
        if arg == "--root" and args:
            root = Path(args.pop(0))
        elif arg == "--disable" and args:
            rule = args.pop(0)
            if rule not in RULES:
                print("check-doc-recipes: unknown rule %r (rules: %s)" % (rule, ", ".join(RULES)), file=sys.stderr)
                return 2
            off.append(rule)
        else:
            print(__doc__, file=sys.stderr)
            return 2
    try:
        findings = run(root, tuple(off))
    except GateError as exc:
        print("check-doc-recipes: error: %s" % exc, file=sys.stderr)
        return 2
    report(findings)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
