#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check the Pelorus 1.0 frozen public surface against current sources.

The manifest records installed headers, public ``pel_*`` and ``pelorus_*`` function declarations,
FFmpeg filter/BSF names, their AVOptions (including ``option=const`` spellings),
encoder AVOptions added by canonical patches, and ``lavfi.pelorus.*`` metadata
string literals. Prototype extraction is a deliberately small C-header regex:
it recognizes ordinary declarations ending in ``;`` after comments are removed,
not preprocessor-generated declarations. Each canonical FFmpeg source currently
owns one filter and one AVOption table; shared ``PEL_VK_POOL_OPTIONS`` rows are
expanded from their defining header.

Exit 0 clean or manifest written, 1 contract findings, 2 the check could not
run because an input or command line was invalid.

    python3 -I scripts/check-frozen-surface.py [--root D]
    python3 -I scripts/check-frozen-surface.py [--root D] --write
    python3 -I scripts/check-frozen-surface.py --self-test
"""

import datetime
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


MANIFEST = Path("libpelorus/frozen-surface.txt")
MESON = Path("libpelorus/meson.build")
FFMPEG_FILES = Path("ffmpeg-patches/files")
POOL_HEADER = FFMPEG_FILES / "pelorus_vulkan_pool.h"
SECTIONS = ("headers", "symbols", "filters", "avoptions", "encoder-avoptions", "metadata-keys")
SECTION_KIND = {
    "headers": "header",
    "symbols": "symbol",
    "filters": "filter",
    "avoptions": "avoption",
    "encoder-avoptions": "encoder-avoption",
    "metadata-keys": "metadata-key",
}
KIND_SECTION = {kind: section for section, kind in SECTION_KIND.items()}


class GateError(Exception):
    """The checker could not evaluate its inputs (exit 2)."""


@dataclass(frozen=True)
class Exemption:
    """One dated exception to a frozen name."""

    kind: str
    name: str
    expiry: datetime.date
    reason: str


def read_text(path):
    """Read one UTF-8 input or turn the failure into a gate error."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateError("cannot read %s: %s" % (path, exc)) from exc


def strip_c_comments(text):
    """Remove comments while retaining quoted strings."""
    pattern = re.compile(r'("(?:\\.|[^"\\])*")|(\'(?:\\.|[^\'\\])*\')|(/\*.*?\*/|//[^\n]*)', re.S)
    return pattern.sub(lambda match: match.group(1) or match.group(2) or " ", text)


def matching_delimiter(text, start, opening, closing):
    """Return the matching delimiter, ignoring quoted C strings and chars."""
    depth = 0
    quote = None
    escaped = False
    for pos in range(start, len(text)):
        char = text[pos]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return pos
    raise GateError("unclosed %s delimiter" % opening)


def initializer_bodies(text, declaration):
    """Return brace bodies following declarations matched by ``declaration``."""
    bodies = []
    for match in re.finditer(declaration, text, re.M):
        start = text.find("{", match.end())
        if start < 0:
            raise GateError("initializer after %r has no opening brace" % match.group(0))
        end = matching_delimiter(text, start, "{", "}")
        bodies.append(text[start + 1:end])
    return bodies


def brace_entries(text):
    """Return top-level brace initializers from an initializer body."""
    entries = []
    pos = 0
    while pos < len(text):
        start = text.find("{", pos)
        if start < 0:
            break
        end = matching_delimiter(text, start, "{", "}")
        entries.append(text[start:end + 1])
        pos = end + 1
    return entries


def header_paths(root):
    """Read the installed header list from ``libpelorus_headers``."""
    text = read_text(root / MESON)
    match = re.search(r"\blibpelorus_headers\s*=\s*files\s*\(", text)
    if not match:
        raise GateError("%s has no libpelorus_headers files() list" % MESON)
    start = text.find("(", match.start())
    end = matching_delimiter(text, start, "(", ")")
    paths = re.findall(r"['\"]([^'\"]+\.h)['\"]", text[start + 1:end])
    if not paths:
        raise GateError("%s libpelorus_headers is empty" % MESON)
    return paths


def public_headers_and_symbols(root):
    """Extract installed names and ordinary public Pelorus prototypes."""
    headers = set()
    symbols = set()
    for rel in header_paths(root):
        path = root / "libpelorus" / rel
        if not path.is_file():
            raise GateError("installed header is missing: %s" % path)
        marker = "include/"
        headers.add(rel.split(marker, 1)[1] if marker in rel else Path(rel).name)
        clean = strip_c_comments(read_text(path))
        for statement in clean.split(";"):
            symbols.update(re.findall(r"\b(pel(?:orus)?_[A-Za-z0-9_]+)\s*\(", statement))
    if not symbols:
        raise GateError("installed headers expose no Pelorus function declarations")
    return headers, symbols


def joined_macro(text, macro):
    """Join one backslash-continued object/function macro definition."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"\s*#\s*define\s+%s\b" % re.escape(macro), line):
            parts = []
            for offset in range(len(lines) - index):
                current = lines[index + offset]
                continued = current.rstrip().endswith("\\")
                parts.append(current.rstrip()[:-1] if continued else current)
                if not continued:
                    return "\n".join(parts)
            raise GateError("macro %s ends with a continuation" % macro)
    raise GateError("macro %s is missing" % macro)


def raw_option_rows(body):
    """Return (name, type, unit) from literal AVOption rows."""
    rows = []
    for entry in brace_entries(body):
        name = re.match(r"\{\s*\"([^\"]+)\"", entry)
        opt_type = re.search(r"\bAV_OPT_TYPE_([A-Z0-9_]+)\b", entry)
        if not name or not opt_type:
            continue
        unit = option_unit(entry)
        rows.append((name.group(1), opt_type.group(1), unit))
    return rows


def option_unit(entry):
    """Return a designated or final positional AVOption unit."""
    designated = re.search(r"\.unit\s*=\s*\"([^\"]+)\"", entry)
    if designated:
        return designated.group(1)
    positional = re.search(r",\s*\"([^\"]+)\"\s*\}\s*,?\s*\\?\s*$", entry, re.S)
    return positional.group(1) if positional else None


def added_row_closed(entry):
    """Say whether an added initializer contains its outer closing ``},``."""
    start = entry.find("{")
    try:
        end = matching_delimiter(entry, start, "{", "}")
    except GateError:
        return False
    return re.fullmatch(r"\s*,\s*\\?\s*", entry[end + 1:]) is not None


def option_names(filter_name, rows):
    """Map AVOption rows to ``filter.option`` and ``filter.option=const``."""
    names = set()
    unit_owner = {}
    for name, opt_type, unit in rows:
        if opt_type == "CONST":
            continue
        names.add("%s.%s" % (filter_name, name))
        if unit:
            unit_owner[unit] = name
    for name, opt_type, unit in rows:
        if opt_type != "CONST":
            continue
        if not unit or unit not in unit_owner:
            raise GateError("%s AVOption const %s has no owning option" % (filter_name, name))
        names.add("%s.%s=%s" % (filter_name, unit_owner[unit], name))
    return names


def added_patch_option_rows(path):
    """Return target paths and typed AVOption rows added by one unified diff."""
    rows = []
    target = None
    in_hunk = False
    pending = None
    pending_target = None
    for line_no, line in enumerate(read_text(path).splitlines(), 1):
        if line.startswith("diff --git "):
            if pending is not None:
                raise GateError("%s:%d: added AVOption row is not closed" % (path, line_no))
            target = None
            in_hunk = False
            continue
        target_match = re.match(r"\+\+\+ b/(.+)$", line)
        if target_match:
            target = target_match.group(1)
            in_hunk = False
            continue
        if line.startswith("@@"):
            if pending is not None:
                raise GateError("%s:%d: added AVOption row is not closed" % (path, line_no))
            if target is None:
                raise GateError("%s:%d: hunk has no +++ b/ target" % (path, line_no))
            in_hunk = True
            continue
        if pending is not None:
            if not line.startswith("+") or line.startswith("+++"):
                raise GateError("%s:%d: added AVOption row is not closed" % (path, line_no))
            pending += "\n" + line[1:]
            if added_row_closed(pending):
                rows.append(typed_added_option(path, pending_target, pending))
                pending = None
                pending_target = None
            continue
        if not in_hunk or not re.match(r"\+\s*\{\s*\"[^\"]+\"\s*,", line):
            continue
        pending = line[1:]
        pending_target = target
        if added_row_closed(pending):
            rows.append(typed_added_option(path, pending_target, pending))
            pending = None
            pending_target = None
    if pending is not None:
        raise GateError("%s: added AVOption row is not closed" % path)
    return rows


def typed_added_option(path, target, entry):
    """Type one added literal row or fail closed."""
    name = re.match(r"\s*\{\s*\"([^\"]+)\"\s*,", entry)
    opt_type = re.search(r"\bAV_OPT_TYPE_([A-Z0-9_]+)\b", entry)
    if not name or not opt_type:
        label = name.group(1) if name else "<unparsed>"
        raise GateError("%s: added row %s in %s has no AV_OPT_TYPE_*" %
                        (path, label, target or "unknown target"))
    return target, (name.group(1), opt_type.group(1), option_unit(entry))


def encoder_patch_options(root):
    """Extract AVOption names added to stock encoders by canonical patches."""
    patch_dir = root / FFMPEG_FILES
    patches = sorted(patch_dir.glob("*.patch"))
    if not patches:
        raise GateError("no canonical FFmpeg patches under %s" % patch_dir)
    grouped = {}
    for path in patches:
        for target, row in added_patch_option_rows(path):
            basename = Path(target).name
            if basename.endswith(".c"):
                basename = basename[:-2]
            grouped.setdefault(basename, []).append(row)
    names = set()
    unit_owner = {}
    for basename, rows in grouped.items():
        for name, opt_type, unit in rows:
            if opt_type == "CONST":
                continue
            names.add("%s.%s" % (basename, name))
            if unit:
                unit_owner[(basename, unit)] = name
    for basename, rows in grouped.items():
        for name, opt_type, unit in rows:
            if opt_type != "CONST" or not unit:
                continue
            owner = unit_owner.get((basename, unit), unit)
            names.add("%s.%s=%s" % (basename, owner, name))
    return names


def filter_name(text):
    """Extract the one FFFilter or FFBitStreamFilter public name in a file."""
    clean = strip_c_comments(text)
    bodies = initializer_bodies(
        clean, r"\bconst\s+(?:FFFilter|FFBitStreamFilter)\s+[A-Za-z0-9_]+\s*=\s*")
    names = []
    for body in bodies:
        match = re.search(r"\.(?:p\.)?name\s*=\s*\"([^\"]+)\"", body)
        if match:
            names.append(match.group(1))
    if len(names) != 1:
        raise GateError("expected one FFFilter/FFBitStreamFilter name, found %d" % len(names))
    return names[0]


def ffmpeg_surface(root):
    """Extract filter, option, and metadata names from canonical sources."""
    source_dir = root / FFMPEG_FILES
    sources = sorted(source_dir.glob("*.c"))
    if not sources:
        raise GateError("no canonical FFmpeg C sources under %s" % source_dir)
    pool_rows = raw_option_rows(joined_macro(read_text(root / POOL_HEADER), "PEL_VK_POOL_OPTIONS"))
    filters = set()
    options = set()
    metadata = set()
    for path in sources:
        text = read_text(path)
        name = filter_name(text)
        filters.add(name)
        clean = strip_c_comments(text)
        tables = initializer_bodies(clean, r"\bstatic\s+const\s+AVOption\s+[A-Za-z0-9_]+\s*\[\s*\]\s*=\s*")
        if len(tables) != 1:
            raise GateError("%s: expected one AVOption table, found %d" % (path, len(tables)))
        rows = raw_option_rows(tables[0])
        if "PEL_VK_POOL_OPTIONS" in tables[0]:
            rows.extend(pool_rows)
        options.update(option_names(name, rows))
        metadata.update(re.findall(r'\"(lavfi\.pelorus\.[A-Za-z0-9_.-]+)\"', text))
    return filters, options, metadata


def extract_surface(root):
    """Return all current surface sections as sets."""
    headers, symbols = public_headers_and_symbols(root)
    filters, options, metadata = ffmpeg_surface(root)
    return {
        "headers": headers,
        "symbols": symbols,
        "filters": filters,
        "avoptions": options,
        "encoder-avoptions": encoder_patch_options(root),
        "metadata-keys": metadata,
    }


def parse_exemption(line, line_no):
    """Parse one ``kind name expiry=... reason=...`` line."""
    match = re.fullmatch(r"(\S+)\s+(\S+)\s+expiry=(\d{4}-\d{2}-\d{2})\s+reason=(.+)", line)
    if not match or match.group(1) not in KIND_SECTION:
        raise GateError("%s:%d: invalid exemption" % (MANIFEST, line_no))
    try:
        expiry = datetime.date.fromisoformat(match.group(3))
    except ValueError as exc:
        raise GateError("%s:%d: invalid exemption expiry" % (MANIFEST, line_no)) from exc
    return Exemption(match.group(1), match.group(2), expiry, match.group(4).strip())


def parse_manifest(text, allow_missing=False):
    """Parse the checked-in manifest and its dated exemptions."""
    values = {section: set() for section in SECTIONS}
    exemptions = []
    section = None
    seen_sections = set()
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        heading = re.fullmatch(r"\[([a-z-]+)\]", line)
        if heading:
            section = heading.group(1)
            if section not in SECTIONS + ("exempt",) or section in seen_sections:
                raise GateError("%s:%d: unknown or repeated section %s" % (MANIFEST, line_no, section))
            seen_sections.add(section)
            continue
        if section is None:
            raise GateError("%s:%d: entry before a section" % (MANIFEST, line_no))
        if section == "exempt":
            exemptions.append(parse_exemption(line, line_no))
        elif line in values[section]:
            raise GateError("%s:%d: duplicate entry %s" % (MANIFEST, line_no, line))
        else:
            values[section].add(line)
    missing = set(SECTIONS + ("exempt",)) - seen_sections
    if missing and not allow_missing:
        raise GateError("%s is missing sections: %s" % (MANIFEST, ", ".join(sorted(missing))))
    return values, exemptions


def render_manifest(surface, exemptions):
    """Render a deterministic manifest."""
    lines = [
        "# Copyright 2026 Lusoris",
        "# SPDX-" + "License-Identifier: EUPL-1.2",
        "# Generated by scripts/check-frozen-surface.py --write; review before committing.",
    ]
    for section in SECTIONS:
        lines.extend(("", "[%s]" % section))
        lines.extend(sorted(surface[section]))
    lines.extend(("", "[exempt]"))
    for item in sorted(exemptions, key=lambda entry: (entry.kind, entry.name)):
        lines.append("%s %s expiry=%s reason=%s" %
                     (item.kind, item.name, item.expiry.isoformat(), item.reason))
    return "\n".join(lines) + "\n"


def compare_surface(expected, actual, exemptions, today):
    """Return removed, unlisted, and expired-exemption findings."""
    findings = []
    exempt = {(item.kind, item.name) for item in exemptions}
    for section in SECTIONS:
        kind = SECTION_KIND[section]
        for name in sorted(expected[section] - actual[section]):
            if (kind, name) not in exempt:
                findings.append(("removed", kind, name, "manifest entry is missing from source"))
        for name in sorted(actual[section] - expected[section]):
            findings.append(("unlisted", kind, name,
                             "source entry is absent from manifest; run --write and review"))
    for item in exemptions:
        if today > item.expiry:
            findings.append(("exempt-expired", item.kind, item.name,
                             "exemption expired %s: %s" % (item.expiry, item.reason)))
    return findings


def run(root, today=None):
    """Load and compare the manifest with source."""
    expected, exemptions = parse_manifest(read_text(root / MANIFEST))
    actual = extract_surface(root)
    return compare_surface(expected, actual, exemptions, today or datetime.date.today())


def write_manifest(root):
    """Rewrite current source names while retaining the exemption list."""
    path = root / MANIFEST
    exemptions = []
    if path.exists():
        _, exemptions = parse_manifest(read_text(path), allow_missing=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(render_manifest(extract_surface(root), exemptions), encoding="utf-8")
    except OSError as exc:
        raise GateError("cannot write %s: %s" % (path, exc)) from exc
    print("check-frozen-surface: wrote %s" % path)


def fake_tree(root):
    """Create the smallest source tree that exercises every surface class."""
    files = {
        MESON: "libpelorus_headers = files('include/pelorus/demo.h')\n",
        Path("libpelorus/include/pelorus/demo.h"): "pel_result pel_alpha(void);\n",
        POOL_HEADER: ("#define PEL_VK_POOL_OPTIONS(a, b, c) \\\n"
                      "    {\"tiling\", \"t\", a, AV_OPT_TYPE_INT, {0}, 0, 1, c}\n"),
        FFMPEG_FILES / "vf_pelorus_demo.c": (
            "static const AVOption demo_options[] = {\n"
            " {\"gain\", \"g\", 0, AV_OPT_TYPE_INT, {0}, 0, 1, 0, .unit=\"gain\"},\n"
            " {\"low\", \"l\", 0, AV_OPT_TYPE_CONST, {0}, 0, 0, 0, .unit=\"gain\"},\n"
            " {NULL}};\n"
            "const FFFilter ff_vf_pelorus_demo = {.p.name = \"pelorus_demo\"};\n"
            "const char *key = \"lavfi.pelorus.score\";\n"),
        FFMPEG_FILES / "demo-encoder.patch": (
            "diff --git a/libavcodec/demoenc.h b/libavcodec/demoenc.h\n"
            "--- a/libavcodec/demoenc.h\n"
            "+++ b/libavcodec/demoenc.h\n"
            "@@ -1,2 +1,4 @@ static const AVOption options[] = {\n"
            "+    { \"pelorus_roi\", \"Honor Pelorus ROI maps\",\n"
            "+      OFFSET(pelorus_roi), AV_OPT_TYPE_BOOL, { .i64 = 0 }, 0, 1, VE },\n"
            "+    { \"mode\", \"Pelorus mode\", 0, AV_OPT_TYPE_INT, { .i64 = 0 }, 0, 1, VE, \"mode\" },\n"
            "+    { \"fast\", \"Fast Pelorus mode\", 0, AV_OPT_TYPE_CONST, { .i64 = 1 }, 0, 0, VE, \"mode\" },\n"
            "     { \"context_only\", \"Stock option\", 0, AV_OPT_TYPE_BOOL, { 0 }, 0, 1, VE },\n"),
    }
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def has_finding(findings, rule, kind=None, name=None):
    """Say whether one expected planted finding was returned."""
    return any(item[0] == rule and (kind is None or item[1] == kind)
               and (name is None or item[2] == name) for item in findings)


def self_test_case(root, label, mutate, expected):
    """Run one mutation, restore its file, and check the expected finding."""
    path, before, after = mutate
    original = read_text(path)
    path.write_text(original.replace(before, after), encoding="utf-8")
    try:
        findings = run(root)
    except GateError as exc:
        print("self-test FAIL: %s raised %s" % (label, exc))
        path.write_text(original, encoding="utf-8")
        return 1
    path.write_text(original, encoding="utf-8")
    if not has_finding(findings, *expected):
        print("self-test FAIL: %s not caught: %s" % (label, findings))
        return 1
    return 0


def encoder_self_test(root, encoder_patch):
    """Check added/context/const classification and fail-closed typing."""
    failures = 0
    surface, _ = parse_manifest(read_text(root / MANIFEST))
    expected = surface["encoder-avoptions"]
    if "demoenc.h.context_only" in expected:
        print("self-test FAIL: context-only encoder AVOption was counted")
        failures += 1
    for name in ("demoenc.h.pelorus_roi", "demoenc.h.mode=fast"):
        if name not in expected:
            print("self-test FAIL: added encoder AVOption was not counted: %s" % name)
            failures += 1
    original = read_text(encoder_patch)
    encoder_patch.write_text(original.replace("AV_OPT_TYPE_BOOL", "UNKNOWN_TYPE", 1),
                             encoding="utf-8")
    try:
        run(root)
    except GateError:
        pass
    else:
        print("self-test FAIL: untyped added encoder row did not fail closed")
        failures += 1
    finally:
        encoder_patch.write_text(original, encoding="utf-8")
    return failures


def self_test():
    """Plant one defect per rule and prove a clean tree passes."""
    failures = 0
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        fake_tree(root)
        write_manifest(root)
        if run(root):
            print("self-test FAIL: generated clean tree reported findings")
            failures += 1
        header = root / "libpelorus/include/pelorus/demo.h"
        source = root / FFMPEG_FILES / "vf_pelorus_demo.c"
        encoder_patch = root / FFMPEG_FILES / "demo-encoder.patch"
        cases = (
            ("removed public symbol", (header, "pel_alpha", "pel_beta"),
             ("removed", "symbol", "pel_alpha")),
            ("renamed AVOption", (source, "\"gain\"", "\"power\""),
             ("removed", "avoption", "pelorus_demo.gain")),
            ("removed metadata key", (source, "lavfi.pelorus.score", "other.score"),
             ("removed", "metadata-key", "lavfi.pelorus.score")),
            ("unlisted addition", (source, "const char *key =", "const char *extra = \"lavfi.pelorus.extra\";\nconst char *key ="),
             ("unlisted", "metadata-key", "lavfi.pelorus.extra")),
            ("renamed encoder AVOption", (encoder_patch, "\"pelorus_roi\"", "\"renamed_roi\""),
             ("removed", "encoder-avoption", "demoenc.h.pelorus_roi")),
            ("removed encoder AVOption", (encoder_patch,
             "+    { \"pelorus_roi\", \"Honor Pelorus ROI maps\",\n"
             "+      OFFSET(pelorus_roi), AV_OPT_TYPE_BOOL, { .i64 = 0 }, 0, 1, VE },\n", ""),
             ("removed", "encoder-avoption", "demoenc.h.pelorus_roi")),
            ("unlisted encoder AVOption", (encoder_patch,
             "     { \"context_only\"", "+    { \"context_only\""),
             ("unlisted", "encoder-avoption", "demoenc.h.context_only")),
        )
        for label, mutate, expected in cases:
            failures += self_test_case(root, label, mutate, expected)
        failures += encoder_self_test(root, encoder_patch)
        surface, exemptions = parse_manifest(read_text(root / MANIFEST))
        expired = Exemption("symbol", "pel_alpha", datetime.date.today() - datetime.timedelta(days=1),
                            "planted expiry")
        (root / MANIFEST).write_text(render_manifest(surface, exemptions + [expired]), encoding="utf-8")
        findings = run(root)
        if not has_finding(findings, "exempt-expired", "symbol", "pel_alpha"):
            print("self-test FAIL: expired exemption not caught: %s" % findings)
            failures += 1
    if failures:
        print("check-frozen-surface self-test: %d failure(s)" % failures)
        return 1
    print("check-frozen-surface self-test: ok")
    return 0


def report(findings):
    """Print stable one-line diagnostics."""
    for rule, kind, name, message in findings:
        print("%s: [%s] %s %s: %s" % (MANIFEST, rule, kind, name, message))
    if findings:
        print("check-frozen-surface: %d finding(s)" % len(findings))
    else:
        print("check-frozen-surface: ok")


def parse_args(argv):
    """Parse the intentionally small command line."""
    root = Path(".")
    write = False
    selftest = False
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--root" and args:
            root = Path(args.pop(0))
        elif arg == "--write":
            write = True
        elif arg == "--self-test":
            selftest = True
        else:
            raise GateError("invalid arguments")
    if selftest and (write or root != Path(".")):
        raise GateError("--self-test cannot be combined with --root or --write")
    return root, write, selftest


def main(argv):
    try:
        root, write, selftest = parse_args(argv)
        if selftest:
            return self_test()
        if write:
            write_manifest(root)
            return 0
        findings = run(root)
    except GateError as exc:
        print("check-frozen-surface: error: %s" % exc, file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return 2
    report(findings)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
