#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Extract, validate, reorder and unwrap changelog sections for GitHub releases.

  release_notes.py VERSION [CHANGELOG] [--repo OWNER/NAME]
      Extract the "## [VERSION]" section from CHANGELOG (default: CHANGELOG.md),
      require "### Highlights" with 3 to 6 bullets, place "### Highlights" first
      and "### Maintenance" last, unwrap paragraphs and list items so each emits
      on one line, and rewrite relative documentation links to absolute blob
      URLs at the release tag (v<VERSION>).
  release_notes.py --unwrap FILE --tag TAG [--repo OWNER/NAME]
      Unwrap an existing release body and rewrite relative documentation links
      to absolute blob URLs at TAG (vX.Y.Z[-rc.N]).
  release_notes.py --self-test
      Verify the fixture, unwrap rules, link rewriting, and planted failure cases.
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

MIN_HIGHLIGHTS = 3
MAX_HIGHLIGHTS = 6
FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING_LINE = re.compile(r"^\s*#{1,6}(?:\s|$)")
LIST_ITEM = re.compile(r"^\s*(?:[-*+] |(?:0|[1-9][0-9]*)\. )")
TOP_LIST_ITEM = re.compile(r"^(?:[-*+] |(?:0|[1-9][0-9]*)\. )")
LINK_DEF = re.compile(r"^\[[^\]]+\]:\s+")
VERSION_LINK_DEF = re.compile(r"^\[(?:Unreleased|[0-9]+\.[0-9]+[^\]]*)\]:\s+")
TAG_PATTERN = re.compile(
    r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-rc\.([1-9][0-9]*))?$"
)
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
INLINE_TOKEN = re.compile(
    r"(?P<comment><!--.*?-->)"
    r"|(?P<code>(?P<ticks>`+).*?(?P=ticks))"
    r"|(?P<link>!?\[(?P<text>(?:\\\]|[^\[\]]|\[[^\[\]]*\])*)\]\((?P<target>[^)]+)\))"
)
FIXTURES_DIR = Path(__file__).with_name("fixtures")
ROOT_DIR = Path(__file__).resolve().parents[2]


def _fence_marker(line: str) -> str | None:
    match = FENCE_OPEN.match(line)
    return None if match is None else match.group(1)


def _closes_fence(line: str, fence: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= len(fence) and set(stripped) == {fence[0]}


def _is_standalone(line: str) -> bool:
    return (
        bool(HEADING_LINE.match(line))
        or line.lstrip().startswith("|")
        or bool(LINK_DEF.match(line))
    )


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


def _rewrite_target(raw_target: str, base_blob_url: str) -> tuple[str, str | None]:
    s = raw_target.strip()
    m = re.match(r"^(<[^>]+>|\S+)(?:\s+(.*))?$", s)
    if not m:
        return raw_target, None
    url_part, rest = m.group(1), (m.group(2) or "")
    is_angle = url_part.startswith("<") and url_part.endswith(">")
    url = url_part[1:-1] if is_angle else url_part

    if url.startswith("#") or url.startswith("/"):
        return raw_target, None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme:
        return raw_target, None

    norm_path = posixpath.normpath(parsed.path)
    if norm_path == ".." or norm_path.startswith("../") or norm_path.startswith("/"):
        return raw_target, f"relative link target escapes repository root: {url!r}"

    rewritten = f"{base_blob_url}/{norm_path}"
    if parsed.query:
        rewritten += f"?{parsed.query}"
    if parsed.fragment:
        rewritten += f"#{parsed.fragment}"

    formatted = f"<{rewritten}>" if is_angle else rewritten
    return (f"{formatted} {rest}" if rest else formatted), None


def _rewrite_inline_links(line: str, base_blob_url: str, errors: list[str]) -> str:
    def repl(m: re.Match[str]) -> str:
        if m.group("comment") or m.group("code"):
            return m.group(0)
        target = m.group("target")
        new_target, err = _rewrite_target(target, base_blob_url)
        if err is not None:
            errors.append(err)
            return m.group(0)
        prefix = m.group(0)[: -len(target) - 1]
        return f"{prefix}{new_target})"

    return INLINE_TOKEN.sub(repl, line)


def rewrite_markdown_links(text: str, base_blob_url: str) -> tuple[str, list[str]]:
    lines = text.splitlines()
    out: list[str] = []
    errors: list[str] = []
    fence: str | None = None
    in_comment = False
    for line in lines:
        if fence is not None:
            out.append(line)
            if _closes_fence(line, fence):
                fence = None
            continue
        if in_comment:
            out.append(line)
            if "-->" in line:
                in_comment = False
            continue
        if (marker := _fence_marker(line)) is not None:
            out.append(line)
            fence = marker
            continue
        if line.lstrip().startswith("<!--"):
            out.append(line)
            if "-->" not in line:
                in_comment = True
            continue
        m_def = re.match(r"^(\s*\[[^\]]+\]:\s+)(.*)$", line)
        if m_def:
            prefix, target = m_def.group(1), m_def.group(2)
            new_target, err = _rewrite_target(target, base_blob_url)
            if err is not None:
                errors.append(err)
                out.append(line)
            else:
                out.append(f"{prefix}{new_target}")
            continue
        out.append(_rewrite_inline_links(line, base_blob_url, errors))
    res = "\n".join(out)
    if text.endswith("\n"):
        res += "\n"
    return res, errors


def _is_relative_target(raw_target: str) -> bool:
    s = raw_target.strip()
    m = re.match(r"^(<[^>]+>|\S+)(?:\s+(.*))?$", s)
    if not m:
        return False
    u = m.group(1)
    if u.startswith("<") and u.endswith(">"):
        u = u[1:-1]
    if u.startswith("#") or u.startswith("/"):
        return False
    p = urllib.parse.urlsplit(u)
    return not bool(p.scheme) and bool(p.path)


def _find_relative_links(text: str) -> list[str]:
    rel: list[str] = []
    fence: str | None = None
    in_comment = False
    for line in text.splitlines():
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
            continue
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if (marker := _fence_marker(line)) is not None:
            fence = marker
            continue
        if line.lstrip().startswith("<!--"):
            if "-->" not in line:
                in_comment = True
            continue
        m_def = re.match(r"^\s*\[[^\]]+\]:\s+(\S+)", line)
        if m_def and _is_relative_target(m_def.group(1)):
            rel.append(m_def.group(1))
            continue
        for m in INLINE_TOKEN.finditer(line):
            if m.group("link") and _is_relative_target(m.group("target")):
                rel.append(m.group("target"))
    return rel


def _resolve_blob_base(
    tag: str, repo: str | None = None, server: str | None = None
) -> tuple[str, str | None]:
    # The environment is read in _parse_cli_args only, so library calls and
    # the self-test give the same result inside and outside GitHub Actions.
    if not repo:
        return "", (
            "repository not specified (pass --repo OWNER/NAME or set GITHUB_REPOSITORY)"
        )
    if not REPO_PATTERN.match(repo):
        return "", f"invalid repository {repo!r} (must be OWNER/NAME)"
    server = (server or "https://github.com").rstrip("/")
    return f"{server}/{repo}/blob/{tag}", None


def _slice_version_section(changelog: str, version: str) -> list[str]:
    header = f"## [{version}]"
    active = False
    collected: list[str] = []
    for line in changelog.splitlines():
        if line.startswith(header):
            active = True
            continue
        if active and (line.startswith("## [") or VERSION_LINK_DEF.match(line)):
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
    changelog: str,
    version: str,
    source: str = "CHANGELOG.md",
    repo: str | None = None,
    server: str | None = None,
    tag: str | None = None,
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
    target_tag = tag or (f"v{version}" if not version.startswith("v") else version)
    if not TAG_PATTERN.match(target_tag):
        errors.append(
            f"{source}: invalid release tag {target_tag!r} (must be vX.Y.Z or vX.Y.Z-rc.N)"
        )
    base_blob_url, repo_err = _resolve_blob_base(target_tag, repo, server)
    if repo_err:
        errors.append(f"{source}: {repo_err}")
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
    full_text = "\n\n".join(rendered) + "\n"
    rewritten_text, rewrite_errors = rewrite_markdown_links(full_text, base_blob_url)
    if rewrite_errors:
        return "", [f"{source}: {e}" for e in rewrite_errors]
    return rewritten_text, []


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
        (
            "relative link escapes root",
            "## [1.0.0]\n\n### Highlights\n\n- item [bad](../secret.md)\n- b\n- c\n",
            "1.0.0",
            "relative link target escapes repository root: '../secret.md'",
        ),
        (
            "reference def escapes root",
            "## [1.0.0]\n\n### Highlights\n\n- a\n- b\n- c\n\n### Added\n\n- item [bad][ref]\n\n[ref]: ../secret.md\n",
            "1.0.0",
            "relative link target escapes repository root: '../secret.md'",
        ),
        (
            "nested traversal escapes root",
            "## [1.0.0]\n\n### Highlights\n\n- item [bad](docs/../../secret.md)\n- b\n- c\n",
            "1.0.0",
            "relative link target escapes repository root: 'docs/../../secret.md'",
        ),
    ]


def _check_shipped_changelog() -> list[str]:
    failures: list[str] = []
    changelog_path = ROOT_DIR / "CHANGELOG.md"
    if not changelog_path.is_file():
        return ["release-notes: CHANGELOG.md not found"]
    text = changelog_path.read_text(encoding="utf-8")
    for ver in ("0.4.0", "0.3.0"):
        notes, errors = extract_version_notes(
            text, ver, "CHANGELOG.md", repo="VMAFx/pelorus"
        )
        if errors:
            failures.append(f"release-notes: CHANGELOG.md [{ver}] failed: {errors}")
            continue
        if not notes.startswith("### Highlights\n"):
            failures.append(f"release-notes: [{ver}] does not start with ### Highlights")
        headings = [l for l in notes.splitlines() if l.startswith("### ")]
        if headings[-1] != "### Maintenance":
            failures.append(f"release-notes: [{ver}] last section is {headings[-1]!r}")
        remaining = _find_relative_links(notes)
        if remaining:
            failures.append(
                f"release-notes: [{ver}] contains un-rewritten relative links: {remaining}"
            )
    return failures


def self_test() -> list[str]:
    failures: list[str] = []
    wrapped = (FIXTURES_DIR / "wrapped-changelog.md").read_text(encoding="utf-8")
    expected = (FIXTURES_DIR / "expected-release-notes.txt").read_text(encoding="utf-8")
    got, errors = extract_version_notes(
        wrapped, "1.2.3", "wrapped-changelog.md", repo="VMAFx/pelorus"
    )
    if errors or got != expected:
        failures.append(f"release-notes: fixture mismatch (errors={errors})")
    for name, src, ver, want_err in _negative_cases():
        _, errs = extract_version_notes(src, ver, "fixture.md", repo="VMAFx/pelorus")
        if not any(want_err in e for e in errs):
            failures.append(f"release-notes: {name} was not refused with {want_err!r}: {errs}")
    _, missing_repo_errs = extract_version_notes(
        wrapped, "1.2.3", "wrapped-changelog.md", repo=""
    )
    if not any("repository not specified" in e for e in missing_repo_errs):
        failures.append("release-notes: missing repository was not refused")

    script = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory() as tmp:
        raw_file = Path(tmp) / "rc-body.md"
        raw_file.write_text(
            "### Added\n\n- Wrapped bullet line one\n  line two with [doc](docs/adr/0185.md).\n",
            encoding="utf-8",
        )
        # Run this program, not the bash wrapper: on the Windows CI leg a bare
        # "bash" resolves to the WSL launcher, which has no distribution.
        done = subprocess.run(
            [sys.executable, "-I", script, "--unwrap", str(raw_file), "--tag", "v1.2.3", "--repo", "VMAFx/pelorus"],
            check=False,
            capture_output=True,
            text=True,
        )
        expected_unwrap = (
            "### Added\n\n"
            "- Wrapped bullet line one line two with "
            "[doc](https://github.com/VMAFx/pelorus/blob/v1.2.3/docs/adr/0185.md).\n"
        )
        if done.returncode != 0 or done.stdout != expected_unwrap:
            failures.append(f"release-notes: --unwrap failed: rc={done.returncode} out={done.stdout!r}")
        missing_tag = subprocess.run(
            [sys.executable, "-I", script, "--unwrap", str(raw_file)],
            check=False,
            capture_output=True,
            text=True,
        )
        if missing_tag.returncode == 0:
            failures.append("release-notes: --unwrap without --tag did not exit nonzero")
        failures += _check_cli_repo_env(script, raw_file, expected_unwrap)
    return failures + _check_shipped_changelog()


def _check_cli_repo_env(script: str, raw_file: Path, expected: str) -> list[str]:
    """The CLI takes the repository from GITHUB_REPOSITORY and refuses without it."""
    base_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GITHUB_REPOSITORY", "GITHUB_SERVER_URL")
    }
    cmd = [sys.executable, "-I", script, "--unwrap", str(raw_file), "--tag", "v1.2.3"]
    failures: list[str] = []
    with_env = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        env={**base_env, "GITHUB_REPOSITORY": "VMAFx/pelorus"},
    )
    if with_env.returncode != 0 or with_env.stdout != expected:
        failures.append(
            f"release-notes: GITHUB_REPOSITORY fallback failed: rc={with_env.returncode}"
        )
    without = subprocess.run(
        cmd, check=False, capture_output=True, text=True, env=base_env
    )
    if without.returncode == 0 or "repository not specified" not in without.stderr:
        failures.append("release-notes: CLI without repository was not refused")
    return failures


def _parse_cli_args(
    argv: list[str],
) -> tuple[dict[str, str | None], list[str], str | None]:
    opts: dict[str, str | None] = {"repo": None, "tag": None, "unwrap": None}
    pos: list[str] = []
    idx = 0
    while idx < len(argv):
        arg = argv[idx]
        if arg in ("--repo", "--tag", "--unwrap"):
            if idx + 1 >= len(argv):
                return opts, pos, f"{arg} requires an argument"
            opts[arg[2:]] = argv[idx + 1]
            idx += 2
        elif arg.startswith("-"):
            return opts, pos, f"unknown option: {arg}"
        else:
            pos.append(arg)
            idx += 1
    if not opts["repo"]:
        opts["repo"] = os.environ.get("GITHUB_REPOSITORY", "").strip() or None
    opts["server"] = os.environ.get("GITHUB_SERVER_URL") or None
    return opts, pos, None


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        failures = self_test()
        if failures:
            print("\n".join(failures), file=sys.stderr)
            return 1
        print("release-notes self-test: ok")
        return 0

    opts, positional, parse_err = _parse_cli_args(argv)
    if parse_err:
        print(f"usage error: {parse_err}", file=sys.stderr)
        return 2

    if opts["unwrap"] is not None:
        if not opts["tag"]:
            print("usage: --unwrap FILE requires companion --tag vX.Y.Z[-rc.N]", file=sys.stderr)
            return 2
        if not TAG_PATTERN.match(opts["tag"]):
            print(f"::error::invalid tag {opts['tag']!r} (must be vX.Y.Z or vX.Y.Z-rc.N)", file=sys.stderr)
            return 1
        base_blob_url, repo_err = _resolve_blob_base(opts["tag"], opts["repo"], opts["server"])
        if repo_err:
            print(f"::error::{repo_err}", file=sys.stderr)
            return 1
        unwrapped = unwrap_markdown(Path(opts["unwrap"]).read_text(encoding="utf-8"))
        rewritten, errs = rewrite_markdown_links(unwrapped, base_blob_url)
        for err in errs:
            print(f"::error::{err}", file=sys.stderr)
        if errs:
            return 1
        sys.stdout.write(rewritten)
        return 0

    if 1 <= len(positional) <= 2:
        ver, ch_path = positional[0], (positional[1] if len(positional) == 2 else "CHANGELOG.md")
        notes, errs = extract_version_notes(
            Path(ch_path).read_text(encoding="utf-8"), ver, ch_path, repo=opts["repo"], tag=opts["tag"], server=opts["server"]
        )
        for err in errs:
            print(f"::error::{err}", file=sys.stderr)
        if errs:
            return 1
        sys.stdout.write(notes)
        return 0

    print(
        "usage: extract-release-notes.sh VERSION [CHANGELOG] [--repo OWNER/NAME]\n"
        "       extract-release-notes.sh --unwrap FILE --tag TAG [--repo OWNER/NAME]\n"
        "       extract-release-notes.sh --self-test",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
