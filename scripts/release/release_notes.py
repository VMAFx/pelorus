#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Extract, validate, reorder and unwrap changelog sections for GitHub releases.

  release_notes.py VERSION [CHANGELOG]
      Extract the "## [VERSION]" section from CHANGELOG (default: CHANGELOG.md),
      require "### Highlights" with 3 to 6 bullets, place "### Highlights" first
      and "### Maintenance" last, and unwrap paragraphs and list items so each
      emits on one line.
  release_notes.py --unwrap FILE
      Unwrap an existing release body (paragraphs and list items on one line;
      fenced code blocks, tables, headings, HTML comments and blank lines kept)
      without requiring "### Highlights".
  release_notes.py --self-test
      Verify the fixture, unwrap rules and planted failure cases.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

MIN_HIGHLIGHTS = 3
MAX_HIGHLIGHTS = 6
FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING_LINE = re.compile(r"^\s*#{1,6}(?:\s|$)")
LIST_ITEM = re.compile(r"^\s*(?:[-*+] |(?:0|[1-9][0-9]*)\. )")
TOP_LIST_ITEM = re.compile(r"^(?:[-*+] |(?:0|[1-9][0-9]*)\. )")
LINK_DEF = re.compile(r"^\[[^\]]+\]:\s+")
FIXTURES_DIR = Path(__file__).with_name("fixtures")
ROOT_DIR = Path(__file__).resolve().parents[2]


def _fence_marker(line: str) -> str | None:
    match = FENCE_OPEN.match(line)
    return None if match is None else match.group(1)


def _closes_fence(line: str, fence: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= len(fence) and set(stripped) == {fence[0]}


def _is_standalone(line: str) -> bool:
    return bool(HEADING_LINE.match(line)) or line.lstrip().startswith("|")


def _trim_blank_edges(lines: list[str]) -> list[str]:
    start = 0
    for idx, line in enumerate(lines):
        if line != "":
            start = idx
            break
    else:
        return []
    end = len(lines)
    for idx in range(len(lines) - 1, start - 1, -1):
        if lines[idx] != "":
            end = idx + 1
            break
    return lines[start:end]


def _step_unwrap(
    line: str,
    out: list[str],
    buf: str | None,
    fence: str | None,
    in_comment: bool,
) -> tuple[str | None, str | None, bool]:
    if fence is not None:
        out.append(line)
        return None, (None if _closes_fence(line, fence) else fence), False
    if in_comment:
        out.append(line)
        return None, None, ("-->" not in line)
    if (marker := _fence_marker(line)) is not None:
        if buf is not None:
            out.append(buf)
        out.append(line)
        return None, marker, False
    if line.lstrip().startswith("<!--"):
        if buf is not None:
            out.append(buf)
        out.append(line)
        return None, None, ("-->" not in line)
    if not line.strip():
        if buf is not None:
            out.append(buf)
        out.append("")
        return None, None, False
    if _is_standalone(line):
        if buf is not None:
            out.append(buf)
        out.append(line.rstrip())
        return None, None, False
    if LIST_ITEM.match(line):
        if buf is not None:
            out.append(buf)
        return line.rstrip(), None, False
    if buf is None:
        return line.rstrip(), None, False
    return f"{buf} {line.strip()}", None, False


def unwrap_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    buf: str | None = None
    fence: str | None = None
    in_comment = False
    for line in lines:
        buf, fence, in_comment = _step_unwrap(line, out, buf, fence, in_comment)
    if buf is not None:
        out.append(buf)
    return out


def unwrap_markdown(text: str) -> str:
    lines = _trim_blank_edges(unwrap_lines(text.splitlines()))
    return ("\n".join(lines) + "\n") if lines else ""


def _slice_version_section(changelog: str, version: str) -> list[str]:
    header = f"## [{version}]"
    active = False
    collected: list[str] = []
    for line in changelog.splitlines():
        if line.startswith(header):
            active = True
            continue
        if active and (line.startswith("## [") or LINK_DEF.match(line)):
            break
        if active:
            collected.append(line)
    return collected


def _parse_subsections(
    lines: list[str],
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    fence: str | None = None
    for line in lines:
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
        else:
            fence = _fence_marker(line)
            if fence is None and line.startswith("### "):
                sections.append((line[4:].strip(), []))
                continue
        if sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return preamble, sections


def _validate_highlights(
    sections: list[tuple[str, list[str]]], source: str, version: str
) -> list[str]:
    hl_blocks = [body for title, body in sections if title == "Highlights"]
    if not hl_blocks:
        return [f"{source}: '## [{version}]' is missing '### Highlights'"]
    if len(hl_blocks) > 1:
        return [f"{source}: '## [{version}]' has duplicate '### Highlights' sections"]
    unwrapped = _trim_blank_edges(unwrap_lines(hl_blocks[0]))
    bullets = [line for line in unwrapped if TOP_LIST_ITEM.match(line)]
    stray = [
        line
        for line in unwrapped
        if line and not LIST_ITEM.match(line) and not line.lstrip().startswith("<!--")
    ]
    errors: list[str] = []
    if stray:
        errors.append(
            f"{source}: '## [{version}]' '### Highlights' must contain bullet items only"
        )
    if not (MIN_HIGHLIGHTS <= len(bullets) <= MAX_HIGHLIGHTS):
        errors.append(
            f"{source}: '## [{version}]' '### Highlights' must have "
            f"{MIN_HIGHLIGHTS} to {MAX_HIGHLIGHTS} bullets (found {len(bullets)})"
        )
    return errors


def extract_version_notes(
    changelog: str, version: str, source: str = "CHANGELOG.md"
) -> tuple[str, list[str]]:
    raw = _slice_version_section(changelog, version)
    if not "".join(raw).strip():
        return "", [
            f"{source} has no non-empty '## [{version}]' section; "
            "the release commit must rotate [Unreleased] into it"
        ]
    preamble, sections = _parse_subsections(raw)
    errors = _validate_highlights(sections, source, version)
    if "".join(preamble).strip():
        errors.append(
            f"{source}: '## [{version}]' has text before the first '###' heading"
        )
    if errors:
        return "", errors
    ordered: list[tuple[str, list[str]]] = [
        ("Highlights", [line for t, b in sections if t == "Highlights" for line in b])
    ]
    for title, body in sections:
        if title not in ("Highlights", "Maintenance"):
            ordered.append((title, body))
    maint = [line for t, b in sections if t == "Maintenance" for line in b]
    if maint:
        ordered.append(("Maintenance", maint))
    rendered: list[str] = []
    for title, body in ordered:
        unwrapped = unwrap_markdown("\n".join(body)).rstrip("\n")
        if unwrapped:
            rendered.append(f"### {title}\n\n{unwrapped}")
    return "\n\n".join(rendered) + "\n", []


def _negative_cases() -> list[tuple[str, str, str, str]]:
    return [
        (
            "missing version",
            "## [1.0.0]\n\n### Highlights\n\n- a\n- b\n- c\n",
            "9.9.9",
            "has no non-empty '## [9.9.9]' section",
        ),
        ("empty version", "## [1.0.0]\n\n## [0.9.0]\n", "1.0.0", "has no non-empty"),
        (
            "missing Highlights",
            "## [1.0.0]\n\n### Added\n\n- item one\n",
            "1.0.0",
            "is missing '### Highlights'",
        ),
        (
            "too few highlights (2)",
            "## [1.0.0]\n\n### Highlights\n\n- one\n- two\n\n### Added\n\n- a\n",
            "1.0.0",
            "3 to 6 bullets (found 2)",
        ),
        (
            "too many highlights (7)",
            "## [1.0.0]\n\n### Highlights\n\n"
            + "".join(f"- item {i}\n" for i in range(1, 8)),
            "1.0.0",
            "3 to 6 bullets (found 7)",
        ),
        (
            "non-bullet in Highlights",
            "## [1.0.0]\n\n### Highlights\n\nParagraph text.\n\n- a\n- b\n- c\n",
            "1.0.0",
            "bullet items only",
        ),
    ]


def _check_shipped_changelog() -> list[str]:
    failures: list[str] = []
    changelog_path = ROOT_DIR / "CHANGELOG.md"
    if not changelog_path.is_file():
        return ["release-notes: CHANGELOG.md not found"]
    text = changelog_path.read_text(encoding="utf-8")
    for ver in ("0.4.0", "0.3.0"):
        notes, errors = extract_version_notes(text, ver, "CHANGELOG.md")
        if errors:
            failures.append(f"release-notes: CHANGELOG.md [{ver}] failed: {errors}")
            continue
        if not notes.startswith("### Highlights\n"):
            failures.append(f"release-notes: [{ver}] does not start with ### Highlights")
        headings = [l for l in notes.splitlines() if l.startswith("### ")]
        if headings[-1] != "### Maintenance":
            failures.append(f"release-notes: [{ver}] last section is {headings[-1]!r}")
    return failures


def self_test() -> list[str]:
    failures: list[str] = []
    wrapped = (FIXTURES_DIR / "wrapped-changelog.md").read_text(encoding="utf-8")
    expected = (FIXTURES_DIR / "expected-release-notes.txt").read_text(encoding="utf-8")
    got, errors = extract_version_notes(wrapped, "1.2.3", "wrapped-changelog.md")
    if errors or got != expected:
        failures.append(f"release-notes: fixture mismatch (errors={errors})")
    for name, src, ver, want_err in _negative_cases():
        _, errs = extract_version_notes(src, ver, "fixture.md")
        if not any(want_err in e for e in errs):
            failures.append(f"release-notes: {name} was not refused with {want_err!r}: {errs}")
    with tempfile.TemporaryDirectory() as tmp:
        raw_file = Path(tmp) / "rc-body.md"
        raw_file.write_text("### Added\n\n- Wrapped bullet line one\n  line two.\n", encoding="utf-8")
        sh = Path(__file__).with_name("extract-release-notes.sh")
        done = subprocess.run(
            ["bash", str(sh), "--unwrap", str(raw_file)],
            check=False,
            capture_output=True,
            text=True,
        )
        if done.returncode != 0 or done.stdout != "### Added\n\n- Wrapped bullet line one line two.\n":
            failures.append(f"release-notes: --unwrap failed: rc={done.returncode} out={done.stdout!r}")
    return failures + _check_shipped_changelog()


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        failures = self_test()
        if failures:
            print("\n".join(failures), file=sys.stderr)
            return 1
        print("release-notes self-test: ok")
        return 0
    if len(argv) == 2 and argv[0] == "--unwrap":
        sys.stdout.write(unwrap_markdown(Path(argv[1]).read_text(encoding="utf-8")))
        return 0
    if 1 <= len(argv) <= 2 and not argv[0].startswith("-"):
        version = argv[0]
        changelog_path = argv[1] if len(argv) == 2 else "CHANGELOG.md"
        text = Path(changelog_path).read_text(encoding="utf-8")
        notes, errors = extract_version_notes(text, version, changelog_path)
        for err in errors:
            print(f"::error::{err}", file=sys.stderr)
        if errors:
            return 1
        sys.stdout.write(notes)
        return 0
    print(
        "usage: extract-release-notes.sh VERSION [CHANGELOG]\n"
        "       extract-release-notes.sh --unwrap FILE\n"
        "       extract-release-notes.sh --self-test",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
