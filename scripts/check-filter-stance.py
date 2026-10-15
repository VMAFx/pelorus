#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Fail when an FFmpeg filter lacks a documented evidence stance.

The source registrations under ``ffmpeg-patches/files`` are the inventory.
README.md must label every registered Pelorus filter and BSF as ``proven`` or
``experimental`` in the table after ``<!-- gate: filter-stance -->``. Proven
rows need a live repository-local evidence link, and every component page must
repeat the README stance and evidence sentence.

Exit 0 clean, 1 findings, 2 the gate could not run (missing or unreadable
input).

    python3 -I scripts/check-filter-stance.py [--root D]
    python3 -I scripts/check-filter-stance.py --self-test
"""

import re
import sys
import tempfile
from pathlib import Path


TABLE_MARK = "<!-- gate: filter-stance -->"
RULES = ("table-missing", "unlabelled", "unknown-row", "proven-no-evidence", "page-mismatch")


class GateError(Exception):
    """The gate could not evaluate its input (exit 2)."""


def run(root):
    """Return stance findings from ``root``."""
    inventory = load_inventory(root)
    readme = read(root, "README.md")
    table, table_finding = load_table(readme)
    if table_finding:
        return [table_finding]

    findings = []
    for name, source in inventory.items():
        row = table.get(name)
        if row is None:
            findings.append(("README.md", source[1], "unlabelled",
                             "%s is registered in %s but has no table row" % (name, source[0])))
            continue
        if row[1] not in ("proven", "experimental"):
            findings.append(("README.md", row[0], "unlabelled",
                             "%s stance must be exactly `proven` or `experimental`" % name))
            continue
        if row[1] == "proven" and not has_evidence(root, row[2]):
            findings.append(("README.md", row[0], "proven-no-evidence",
                             "%s is proven without a live docs/research or docs/hardware-reports link" % name))

    for name, row in table.items():
        if name not in inventory:
            findings.append(("README.md", row[0], "unknown-row",
                             "%s has a table row but no source registration" % name))

    pages = load_pages(root)
    for name, source in inventory.items():
        row = table.get(name)
        if row is None or row[1] not in ("proven", "experimental"):
            continue
        matches = pages.get(name, [])
        if not matches:
            findings.append((source[0], source[1], "page-mismatch",
                             "%s has no per-filter page with a **Stance:** line" % name))
            continue
        if len(matches) > 1:
            findings.append((matches[0][0], matches[0][1], "page-mismatch",
                             "%s has more than one per-filter page" % name))
            continue
        path, line, stance, sentence = matches[0]
        if stance is None:
            findings.append((path, line, "page-mismatch",
                             "%s page has no **Stance:** line" % name))
        elif stance != row[1]:
            findings.append((path, line, "page-mismatch",
                             "%s page says `%s`; README says `%s`" % (name, stance, row[1])))
        elif sentence != row[2]:
            findings.append((path, line, "page-mismatch",
                             "%s page stance sentence differs from README" % name))
    return findings


def read(root, rel):
    try:
        return (root / rel).read_text(encoding="utf-8")
    except OSError as exc:
        raise GateError("cannot read %s: %s" % (rel, exc))


def line_number(text, offset):
    return text.count("\n", 0, offset) + 1


def load_inventory(root):
    source_dir = root / "ffmpeg-patches/files"
    if not source_dir.is_dir():
        raise GateError("cannot read source directory ffmpeg-patches/files")
    sources = sorted(source_dir.glob("vf_pelorus_*.c"))
    sources += sorted(source_dir.glob("*pelorus*bsf.c"))
    if not sources:
        raise GateError("no Pelorus filter or BSF sources found in ffmpeg-patches/files")
    inventory = {}
    for path in sources:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GateError("cannot read %s: %s" % (path.relative_to(root), exc))
        matches = list(re.finditer(r'\.p\.name\s*=\s*"(pelorus_[^"]+)"', text))
        rel = str(path.relative_to(root))
        if len(matches) != 1:
            raise GateError("%s must contain exactly one Pelorus .p.name registration" % rel)
        name = matches[0].group(1)
        if name in inventory:
            raise GateError("duplicate source registration %s" % name)
        inventory[name] = (rel, line_number(text, matches[0].start()))
    return inventory


def split_row(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def load_table(text):
    lines = text.splitlines()
    marker = next((i for i, line in enumerate(lines) if line.strip() == TABLE_MARK), None)
    if marker is None:
        return {}, ("README.md", 1, "table-missing", "filter stance gate marker or table is missing")
    start = marker + 1
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start + 1 >= len(lines) or not lines[start].lstrip().startswith("|"):
        return {}, ("README.md", marker + 1, "table-missing", "no table follows the filter stance marker")
    header = split_row(lines[start])
    expected = ["Filter", "Purpose", "Stance", "Evidence / what promotes it"]
    if header != expected or not re.match(r"^\s*\|(?:\s*:?-+:?\s*\|){4}\s*$", lines[start + 1]):
        return {}, ("README.md", start + 1, "table-missing",
                    "filter stance table must have the Filter, Purpose, Stance, and Evidence / what promotes it columns")
    table = {}
    for index in range(start + 2, len(lines)):
        line = lines[index]
        if not line.lstrip().startswith("|"):
            break
        cells = split_row(line)
        if len(cells) != 4:
            continue
        name_match = re.match(r"^`(pelorus_[^`]+)`(?:\s+\(BSF\))?$", cells[0])
        if not name_match:
            continue
        name = name_match.group(1)
        stance_match = re.match(r"^`(proven|experimental)`$", cells[2])
        stance = stance_match.group(1) if stance_match else None
        if name in table:
            stance = None
        table[name] = (index + 1, stance, cells[3])
    return table, None


def has_evidence(root, cell):
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", cell):
        target = match.group(1).strip().strip("<>").split("#", 1)[0].split("?", 1)[0]
        path = Path(target)
        if target.startswith(("docs/research/", "docs/hardware-reports/")) \
                and ".." not in path.parts and (root / path).is_file():
            return True
    return False


def load_pages(root):
    metrics = root / "docs/metrics"
    usage = root / "docs/usage/grain-fgs-bsf.md"
    if not metrics.is_dir():
        raise GateError("cannot read page directory docs/metrics")
    paths = sorted(metrics.glob("*.md"))
    if usage.is_file():
        paths.append(usage)
    pages = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GateError("cannot read %s: %s" % (path.relative_to(root), exc))
        heading = next((line for line in text.splitlines() if line.startswith("# ")), "")
        names = re.findall(r"(?:vf_)?(pelorus_[a-z0-9_]+)", heading)
        if not names:
            continue
        stance_match = re.search(r"^\*\*Stance:\*\*\s+`([^`]+)`\s+—\s+(\S.*)$", text, re.M)
        stance = stance_match.group(1) if stance_match else None
        sentence = stance_match.group(2) if stance_match else None
        line = line_number(text, stance_match.start()) if stance_match else 1
        rel = str(path.relative_to(root))
        for name in names:
            pages.setdefault(name, []).append((rel, line, stance, sentence))
    return pages


def write_tree(root):
    files = {
        "ffmpeg-patches/files/vf_pelorus_alpha.c": '.p.name = "pelorus_alpha_vulkan",\n',
        "ffmpeg-patches/files/h265_pelorus_fgs_bsf.c": '.p.name = "pelorus_fgs",\n',
        "README.md": (
            TABLE_MARK + "\n\n"
            "| Filter | Purpose | Stance | Evidence / what promotes it |\n"
            "|---|---|---|---|\n"
            "| `pelorus_alpha_vulkan` | Alpha | `proven` | Used for alpha today; its measured pinned-public-corpus result is [evidence](docs/research/alpha.md). |\n"
            "| `pelorus_fgs` (BSF) | Grain | `experimental` | Used for grain today; a public benchmark would promote it. |\n"
        ),
        "docs/metrics/alpha.md": (
            "# vf_pelorus_alpha_vulkan\n\n"
            "**Stance:** `proven` — Used for alpha today; its measured pinned-public-corpus result is [evidence](docs/research/alpha.md).\n"
        ),
        "docs/usage/grain-fgs-bsf.md": (
            "# pelorus_fgs\n\n"
            "**Stance:** `experimental` — Used for grain today; a public benchmark would promote it.\n"
        ),
        "docs/research/alpha.md": "# Measured alpha result on the pinned public corpus\n",
    }
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def plant(root, rule):
    readme = root / "README.md"
    text = readme.read_text(encoding="utf-8")
    if rule == "table-missing":
        readme.write_text("# No stance table\n", encoding="utf-8")
    elif rule == "unlabelled":
        readme.write_text("\n".join(line for line in text.splitlines()
                                     if "pelorus_alpha_vulkan" not in line) + "\n", encoding="utf-8")
    elif rule == "unknown-row":
        readme.write_text(text + "| `pelorus_unknown` | Unknown | `experimental` | Needs proof. |\n",
                          encoding="utf-8")
    elif rule == "proven-no-evidence":
        readme.write_text(text.replace("[evidence](docs/research/alpha.md)", "evidence"),
                          encoding="utf-8")
        page = root / "docs/metrics/alpha.md"
        page.write_text(page.read_text(encoding="utf-8").replace(
            "[evidence](docs/research/alpha.md)", "evidence"), encoding="utf-8")
    elif rule == "page-mismatch":
        page = root / "docs/metrics/alpha.md"
        page.write_text(page.read_text(encoding="utf-8").replace("`proven`", "`experimental`"),
                        encoding="utf-8")
    elif rule == "page-sentence-drift":
        page = root / "docs/metrics/alpha.md"
        page.write_text(page.read_text(encoding="utf-8").replace("Used for alpha today",
                                                                  "Used for changed alpha today"),
                        encoding="utf-8")


def self_test():
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "clean"
        write_tree(clean)
        findings = run(clean)
        if findings:
            failures += 1
            print("self-test FAIL: clean tree: %s" % findings)
    cases = [(rule, rule) for rule in RULES] + [("page-sentence-drift", "page-mismatch")]
    for planted, expected in cases:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_tree(root)
            plant(root, planted)
            try:
                findings = run(root)
            except GateError as exc:
                failures += 1
                print("self-test FAIL: %s raised: %s" % (expected, exc))
                continue
            got = [finding[2] for finding in findings]
            if got != [expected]:
                failures += 1
                print("self-test FAIL: %s: expected [%s], got %s" % (expected, expected, got))
    if failures:
        print("check-filter-stance self-test: %d failure(s)" % failures)
        return 1
    print("check-filter-stance self-test: ok")
    return 0


def report(findings):
    for path, line, rule, message in findings:
        print("%s:%d: [%s] %s" % (path, line, rule, message))
    print("check-filter-stance: %d finding(s)" % len(findings))


def main(argv):
    root = Path(".")
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--self-test" and not args:
            return self_test()
        if arg == "--root" and args:
            root = Path(args.pop(0))
        else:
            print(__doc__, file=sys.stderr)
            return 2
    try:
        findings = run(root)
    except GateError as exc:
        print("check-filter-stance: error: %s" % exc, file=sys.stderr)
        return 2
    report(findings)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
