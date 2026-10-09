#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Release tag rules for final releases and release candidates (ADR-0176).

  verify-release.py tag TAG [--tags-file FILE]
      Tag shape and rc numbering against the tags that already exist (one name
      per line; default: `git tag --list`). Prints `prerelease=...`,
      `latest=...` and `base=...` lines for GITHUB_OUTPUT.
  verify-release.py version TAG --meson-version V --header PATH
      The tag, the meson.build version and pelorus.h agree, number for number.
  verify-release.py --self-test
      Plants each defect and requires its rejection.

Rules: the tag is `vX.Y.Z` or `vX.Y.Z-rc.N` (no leading zeros, N >= 1).
`rc.N` needs `rc.(N-1)` to exist and no later rc and no final tag of the same
version. A final `vX.Y.0` from 0.4.0 on needs at least one rc of that version.
An rc is a prerelease and never `latest`. The version string of an rc is the
full tag without `v`; there is no fallback to the numeric core.

Exit status: 0 accepted, 1 rejected (each reason on stderr), 2 usage.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TAG = re.compile(
    r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-rc\.([1-9][0-9]*))?"
)
# From this minor on, every X.Y.0 goes through a release candidate (ADR-0176).
RC_REQUIRED_FROM = (0, 4)


def parse_tag(tag: str) -> tuple[tuple[int, int, int], int | None] | None:
    match = TAG.fullmatch(tag)
    if match is None:
        return None
    major, minor, patch, rc = match.groups()
    return (int(major), int(minor), int(patch)), (None if rc is None else int(rc))


def check_tag(tag: str, existing: list[str]) -> tuple[list[str], dict[str, str]]:
    """Return (errors, outputs) for one tag against the existing tag names."""
    parsed = parse_tag(tag)
    if parsed is None:
        return [f"{tag!r} is not vX.Y.Z or vX.Y.Z-rc.N (no leading zeros, N >= 1)"], {}
    core, rc = parsed
    base = "{}.{}.{}".format(*core)
    others = [t for t in existing if t != tag]
    rcs = sorted(
        n
        for t in others
        if (p := parse_tag(t)) is not None and p[0] == core and (n := p[1]) is not None
    )
    errors: list[str] = []
    if rc is None:
        needs_rc = core[:2] >= RC_REQUIRED_FROM and core[2] == 0
        if needs_rc and not rcs:
            errors.append(f"{tag}: v{base} needs a release candidate first (v{base}-rc.1)")
    else:
        if f"v{base}" in others:
            errors.append(f"{tag}: v{base} is already released")
        if rc > 1 and (rc - 1) not in rcs:
            errors.append(f"{tag}: v{base}-rc.{rc - 1} does not exist; rc numbers do not skip")
        if any(n >= rc for n in rcs):
            errors.append(f"{tag}: v{base}-rc.{max(rcs)} already exists; rc numbers only increase")
    outputs = {
        "base": base,
        "prerelease": "true" if rc is not None else "false",
        "latest": "false" if rc is not None else "true",
    }
    return errors, outputs


def _macro(header: str, name: str) -> str | None:
    match = re.search(rf'^#define\s+{name}\s+"?([^"\s]+)"?\s*$', header, re.MULTILINE)
    return None if match is None else match.group(1)


def check_version(tag: str, meson_version: str, header: str) -> list[str]:
    """The tag, meson.build and pelorus.h name one version."""
    parsed = parse_tag(tag)
    if parsed is None:
        return [f"{tag!r} is not a release tag"]
    core, _ = parsed
    version = tag[1:]
    errors: list[str] = []
    if meson_version != version:
        errors.append(f"{tag}: meson.build version is {meson_version!r}, expected {version!r}")
    string = _macro(header, "PELORUS_VERSION_STR")
    if string != version:
        errors.append(f"{tag}: pelorus.h PELORUS_VERSION_STR is {string!r}, expected {version!r}")
    numbers = tuple(_macro(header, f"PELORUS_VERSION_{part}") for part in ("MAJOR", "MINOR", "PATCH"))
    if numbers != tuple(str(n) for n in core):
        errors.append(f"{tag}: pelorus.h PELORUS_VERSION_MAJOR/MINOR/PATCH are {numbers}, expected {core}")
    return errors


def git_tags() -> list[str]:
    done = subprocess.run(
        ["git", "tag", "--list"], check=True, capture_output=True, text=True
    )
    return done.stdout.split()


def _header(major: int, minor: int, patch: int, string: str) -> str:
    return (
        f"#define PELORUS_VERSION_MAJOR {major}\n#define PELORUS_VERSION_MINOR {minor}\n"
        f'#define PELORUS_VERSION_PATCH {patch}\n#define PELORUS_VERSION_STR "{string}"\n'
    )


def self_test() -> list[str]:
    """Each case names the error fragment that must appear; None means accepted."""
    failures: list[str] = []
    cases: list[tuple[str, str, list[str], str | None]] = [
        ("rc.1 first", "v0.4.0-rc.1", [], None),
        ("rc.2 after rc.1", "v0.4.0-rc.2", ["v0.4.0-rc.1"], None),
        ("pushed tag already listed", "v0.4.0-rc.2", ["v0.4.0-rc.1", "v0.4.0-rc.2"], None),
        ("rc.3 without rc.2", "v0.4.0-rc.3", ["v0.4.0-rc.1"], "do not skip"),
        ("rc.2 without rc.1", "v0.4.0-rc.2", [], "do not skip"),
        ("rc.2 repeated after rc.3", "v0.4.0-rc.2", ["v0.4.0-rc.1", "v0.4.0-rc.3"], "only increase"),
        ("rc after final", "v0.4.0-rc.2", ["v0.4.0-rc.1", "v0.4.0"], "already released"),
        ("rc of another version", "v0.4.0-rc.2", ["v0.5.0-rc.1"], "do not skip"),
        ("final after rc", "v0.4.0", ["v0.4.0-rc.1"], None),
        ("final minor without rc", "v0.4.0", [], "needs a release candidate"),
        ("final 1.0.0 without rc", "v1.0.0", ["v0.9.0"], "needs a release candidate"),
        ("final patch without rc", "v0.4.1", ["v0.4.0"], None),
        ("plain 0.3.0 cut", "v0.3.0", [], None),
        ("leading zero rc", "v0.4.0-rc.01", [], "not vX.Y.Z"),
        ("rc without number", "v0.4.0-rc", [], "not vX.Y.Z"),
        ("rc.0", "v0.4.0-rc.0", [], "not vX.Y.Z"),
        ("other suffix", "v0.4.0-beta.1", [], "not vX.Y.Z"),
        ("upper case suffix", "v0.4.0-RC.1", [], "not vX.Y.Z"),
        ("build metadata", "v0.4.0-rc.1+b1", [], "not vX.Y.Z"),
        ("leading zero minor", "v0.04.0-rc.1", [], "not vX.Y.Z"),
        ("no v", "0.4.0-rc.1", [], "not vX.Y.Z"),
        ("trailing newline", "v0.4.0-rc.1\n", [], "not vX.Y.Z"),
        ("empty", "", [], "not vX.Y.Z"),
    ]
    for name, tag, existing, expected in cases:
        errors, _ = check_tag(tag, existing)
        if expected is None and errors:
            failures.append(f"verify-release: {name} was rejected: {errors}")
        if expected is not None and not any(expected in e for e in errors):
            failures.append(f"verify-release: {name} was accepted")
    _, rc_out = check_tag("v0.4.0-rc.1", [])
    _, final_out = check_tag("v0.3.0", [])
    if rc_out.get("prerelease") != "true" or rc_out.get("latest") != "false":
        failures.append("verify-release: an rc is not a prerelease that stays off latest")
    if final_out.get("prerelease") != "false" or final_out.get("latest") != "true":
        failures.append("verify-release: a final tag is not a latest release")
    version_cases = [
        ("rc agrees", "v0.4.0-rc.1", "0.4.0-rc.1", _header(0, 4, 0, "0.4.0-rc.1"), None),
        ("final agrees", "v0.4.0", "0.4.0", _header(0, 4, 0, "0.4.0"), None),
        ("meson still final on an rc tag", "v0.4.0-rc.1", "0.4.0", _header(0, 4, 0, "0.4.0-rc.1"), "meson.build version"),
        ("header still final on an rc tag", "v0.4.0-rc.1", "0.4.0-rc.1", _header(0, 4, 0, "0.4.0"), "PELORUS_VERSION_STR"),
        ("rc version left on a final tag", "v0.4.0", "0.4.0-rc.1", _header(0, 4, 0, "0.4.0-rc.1"), "meson.build version"),
        ("rc number differs", "v0.4.0-rc.2", "0.4.0-rc.1", _header(0, 4, 0, "0.4.0-rc.2"), "meson.build version"),
        ("numeric core differs", "v0.4.0-rc.1", "0.4.0-rc.1", _header(0, 3, 0, "0.4.0-rc.1"), "MAJOR/MINOR/PATCH"),
        ("header macro missing", "v0.4.0-rc.1", "0.4.0-rc.1", "", "PELORUS_VERSION_STR"),
    ]
    for name, tag, meson, header, expected in version_cases:
        errors = check_version(tag, meson, header)
        if expected is None and errors:
            failures.append(f"verify-release: {name} was rejected: {errors}")
        if expected is not None and not any(expected in e for e in errors):
            failures.append(f"verify-release: {name} was accepted")
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")
    tag_cmd = sub.add_parser("tag")
    tag_cmd.add_argument("tag")
    tag_cmd.add_argument("--tags-file")
    version_cmd = sub.add_parser("version")
    version_cmd.add_argument("tag")
    version_cmd.add_argument("--meson-version", required=True)
    version_cmd.add_argument("--header", required=True)
    args = parser.parse_args(argv)
    if args.self_test:
        failures = self_test()
        print("\n".join(failures), file=sys.stderr)
        return 1 if failures else 0
    if args.command == "tag":
        existing = (
            Path(args.tags_file).read_text(encoding="utf-8").split()
            if args.tags_file
            else git_tags()
        )
        errors, outputs = check_tag(args.tag, existing)
        if not errors:
            print("\n".join(f"{k}={v}" for k, v in outputs.items()))
    elif args.command == "version":
        header = Path(args.header).read_text(encoding="utf-8")
        errors = check_version(args.tag, args.meson_version, header)
    else:
        parser.print_usage(sys.stderr)
        return 2
    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
