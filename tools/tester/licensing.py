#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Licence record gate of the tester image (ADR-0178, #236).

`licensing.json` records every component a tester image ships: name, version,
SPDX licence, the paths it owns in the image, where its source is, and whether
it may be redistributed. This program checks a finished image tree against
that record and writes the notices file that travels inside the image.

    licensing.py record      [--record FILE] [--repo DIR]
    licensing.py check       --root DIR --commit SHA --ffmpeg-remote URL --ffmpeg-commit SHA
    licensing.py notices     --root DIR --commit SHA --ffmpeg-remote URL --ffmpeg-commit SHA
    licensing.py self-test

`check` exits 1 when a file belongs to no component, a licence is unknown or
not redistributable, a Debian package has no copyright file or comes from
outside the permitted archive components, a licence text is missing, a record
component matches no file, or the notices file is absent or stale. `record`
checks the record itself and its version pins against the repository and
needs no image. `self-test` plants each defect in a fake tree and requires the
check to refuse it; the Containerfile runs it before the real check.

Debian's python3-minimal is the only interpreter in the image, so this file
uses no shutil, tempfile, urllib or http. Run it as `python3 -I -B`.
"""

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECORD = HERE / "licensing.json"
# Built by concatenation so that REUSE does not read this file's own strings as headers.
SPDX_TAG = "SPDX-" + "License-Identifier:"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
URL_RE = re.compile(r"^https://[^\s]+$")
COMPONENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SKIPPED_TOP = frozenset({"proc", "sys", "dev"})
USR_ALIASES = ("bin", "sbin", "lib", "lib32", "lib64", "libx32")
SPDX_OPERATORS = frozenset({"AND", "OR", "WITH"})
HEADER_LINES = 25
MAX_FILES = 2_000_000
MAX_LINK_DEPTH = 40
REQUIRED_COMPONENT_KEYS = ("id", "name", "version", "spdx", "source", "redistributable")
DEP5_NAME_RE = re.compile(
    r"^License:\s*([A-Za-z0-9][A-Za-z0-9.+_-]*(?: (?:or|and|with|WITH) [A-Za-z0-9.+_-]+)*)\s*$"
)


class RecordError(RuntimeError):
    """The record or the arguments are unusable."""


def load_record(path=RECORD):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as err:
        raise RecordError(f"{path}: {err}") from err
    if not isinstance(record, dict):
        raise RecordError(f"{path}: top level must be an object")
    return record


# --- paths and globs ---------------------------------------------------------


def glob_regex(pattern):
    """`**` crosses directories, `*` and `?` do not; the rest is literal."""
    out = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif char == "*":
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    return re.compile("".join(out) + r"\Z")


def compile_globs(patterns):
    return [glob_regex(p) for p in patterns]


def matches(compiled, rel):
    return any(rx.match(rel) for rx in compiled)


def usr_prefix(root):
    """Top-level names that are symlinks into /usr (merged-usr layout)."""
    return tuple(n for n in USR_ALIASES if (Path(root) / n).is_symlink())


def canonical(rel, aliases):
    head = rel.split("/", 1)[0]
    return "usr/" + rel if head in aliases else rel


def resolve_in_root(root, rel):
    """Follow symlinks without leaving the tree; None when the target is absent."""
    root = Path(root)
    parts = [p for p in rel.split("/") if p]
    for _ in range(MAX_LINK_DEPTH):
        current = root
        redo = False
        for index, part in enumerate(parts):
            current = current / part
            if current.is_symlink():
                target = os.readlink(current)
                base = parts[:index]
                parts = _join_link(base, target) + parts[index + 1:]
                redo = True
                break
        if not redo:
            return current if current.exists() else None
    return None


def _join_link(base, target):
    pieces = [] if target.startswith("/") else list(base)
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if pieces:
                pieces.pop()
        else:
            pieces.append(part)
    return pieces


def walk_tree(root):
    """Relative paths of every file and symlink; symlinked directories count as files."""
    found = []
    for base, dirs, files in os.walk(root, followlinks=False):
        rel_base = os.path.relpath(base, root)
        rel_base = "" if rel_base == "." else rel_base
        if rel_base == "":
            dirs[:] = [d for d in dirs if d not in SKIPPED_TOP]
        for name in list(dirs):
            if os.path.islink(os.path.join(base, name)):
                dirs.remove(name)
                files.append(name)
        for name in files:
            found.append(f"{rel_base}/{name}" if rel_base else name)
        if len(found) > MAX_FILES:
            raise RecordError(f"more than {MAX_FILES} files under {root}")
    return sorted(found)


# --- dpkg --------------------------------------------------------------------


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def dpkg_packages(root):
    """Installed packages from /var/lib/dpkg/status: name, version, section, arch."""
    text = read_text(Path(root) / "var/lib/dpkg/status")
    if text is None:
        return None
    packages = []
    for stanza in text.split("\n\n"):
        fields = {}
        for line in stanza.splitlines():
            if line and not line[0].isspace() and ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
        if "Package" in fields and fields.get("Status", "").endswith(" installed"):
            packages.append(fields)
    return sorted(packages, key=lambda f: f["Package"])


def dpkg_owned(root, packages, aliases):
    owned = {}
    info = Path(root) / "var/lib/dpkg/info"
    for fields in packages:
        name = fields["Package"]
        for candidate in (f"{name}.list", f"{name}:{fields.get('Architecture', '')}.list"):
            text = read_text(info / candidate)
            if text is None:
                continue
            for line in text.splitlines():
                rel = line.strip().lstrip("/")
                if rel:
                    owned.setdefault(canonical(rel, aliases), name)
            break
    return owned


def copyright_path(root, package):
    path = resolve_in_root(root, f"usr/share/doc/{package}/copyright")
    if path is not None and path.is_file() and path.stat().st_size > 0:
        return path
    return None


def declared_licences(path):
    names = []
    text = read_text(path) or ""
    if "Format:" not in text[:2000]:
        return []
    for line in text.splitlines():
        match = DEP5_NAME_RE.match(line)
        if match and match.group(1) not in names:
            names.append(match.group(1))
    return sorted(names)


# --- the record --------------------------------------------------------------


def spdx_ids(expression):
    return [t for t in re.split(r"[\s()]+", expression) if t and t not in SPDX_OPERATORS]


def licence_problems(record, expression, what):
    problems = []
    registry = record.get("licences", {})
    ids = spdx_ids(expression)
    if not ids:
        problems.append(f"{what}: empty SPDX expression")
    for ident in ids:
        entry = registry.get(ident)
        if entry is None:
            problems.append(f"{what}: licence {ident} is not in the record's licence registry")
        elif entry.get("redistributable") is not True:
            problems.append(f"{what}: licence {ident} is not redistributable")
    return problems


def component_shape_problems(component):
    cid = component.get("id", "?")
    problems = [f"component {cid}: missing {k}" for k in REQUIRED_COMPONENT_KEYS if k not in component]
    if "id" in component and not COMPONENT_ID_RE.match(str(component["id"])):
        problems.append(f"component {cid}: id must match {COMPONENT_ID_RE.pattern}")
    if component.get("redistributable") is not True:
        problems.append(f"component {cid}: redistributable is not true")
    if not (str(component.get("source", "")).startswith("https://") or component.get("source_in")):
        problems.append(f"component {cid}: source must be an https URL or name source_in")
    kind = component.get("kind", "files")
    if kind not in ("files", "dpkg", "embedded", "lock"):
        problems.append(f"component {cid}: unknown kind {kind}")
    if kind == "dpkg" and not component.get("packages"):
        problems.append(f"component {cid}: kind dpkg needs packages")
    if kind in ("files", "lock") and not component.get("paths"):
        problems.append(f"component {cid}: kind {kind} needs paths")
    if kind == "embedded" and not component.get("embedded_in"):
        problems.append(f"component {cid}: kind embedded needs embedded_in")
    if kind == "lock" and not component.get("lock"):
        problems.append(f"component {cid}: kind lock needs lock")
    return problems


def record_problems(record):
    problems = []
    for key in ("schema_version", "repository", "notices_path", "licences", "components", "ignore", "debian"):
        if key not in record:
            problems.append(f"record: missing {key}")
    if problems:
        return problems
    if not URL_RE.match(str(record["repository"])):
        problems.append("record: repository must be an https URL")
    seen = set()
    for component in record["components"]:
        problems.extend(component_shape_problems(component))
        cid = component.get("id")
        if cid in seen:
            problems.append(f"component {cid}: duplicate id")
        seen.add(cid)
        if "spdx" in component:
            problems.extend(licence_problems(record, str(component["spdx"]), f"component {cid}"))
    ids = {c.get("id") for c in record["components"]}
    for component in record["components"]:
        if component.get("kind") == "embedded" and component.get("embedded_in") not in ids:
            problems.append(f"component {component.get('id')}: embedded_in names no component")
    for ident, entry in record["licences"].items():
        if not isinstance(entry.get("redistributable"), bool):
            problems.append(f"licence {ident}: redistributable must be true or false")
    today = datetime.date.today().isoformat()
    for rule in record["ignore"]:
        if not rule.get("paths") or not rule.get("why") or not rule.get("expires"):
            problems.append("record: every ignore rule needs paths, why and expires")
        elif not DATE_RE.match(str(rule["expires"])):
            problems.append(f"record: ignore rule {rule['paths'][0]}: expires must be YYYY-MM-DD")
        elif str(rule["expires"]) < today:
            problems.append(f"record: ignore rule {rule['paths'][0]} expired on {rule['expires']}: renew it with a reason or remove it")
    return problems


def pin_problems(record, repo):
    """Versions that the repository pins elsewhere must match the record."""
    problems = []
    for component in record["components"]:
        pin = component.get("version_from")
        if not pin:
            continue
        name, _, key = pin.partition(":")
        text = read_text(Path(repo) / name)
        match = re.search(rf"^{re.escape(key)}=(\S+)$", text or "", re.MULTILINE)
        if match is None:
            problems.append(f"component {component['id']}: version_from {pin} not found")
        elif match.group(1) != component["version"]:
            problems.append(
                f"component {component['id']}: version {component['version']} differs from {pin} ({match.group(1)})"
            )
    return problems


def cmd_record(args):
    record = load_record(args.record)
    problems = record_problems(record)
    if not problems:
        problems = pin_problems(record, args.repo)
    return report(problems, "licence record")


# --- claiming ----------------------------------------------------------------


class Claims:
    def __init__(self, record):
        self.ignore = [(compile_globs(r["paths"]), r) for r in record["ignore"]]
        self.components = []
        for component in record["components"]:
            if component.get("kind", "files") in ("files", "lock"):
                self.components.append((compile_globs(component["paths"]), component))

    def owner(self, rel):
        for compiled, rule in self.ignore:
            if matches(compiled, rel):
                return "ignore", rule
        for compiled, component in self.components:
            if matches(compiled, rel):
                return "component", component
        return None, None


def debian_problems(record, root, packages, aliases):
    problems = []
    policy = record["debian"]
    allowed = set(policy.get("components", ["main"]))
    for fields in packages:
        name = fields["Package"]
        section = fields.get("Section", "")
        component = section.split("/", 1)[0] if "/" in section else "main"
        if component not in allowed:
            problems.append(f"debian package {name}: archive component {component} is not permitted ({sorted(allowed)})")
        if copyright_path(root, name) is None:
            problems.append(f"debian package {name}: usr/share/doc/{name}/copyright is missing or empty")
    sources = read_text(Path(root) / policy.get("sources_file", "")) if policy.get("sources_file") else None
    if sources is not None:
        for line in sources.splitlines():
            if line.startswith("Components:") and set(line.split(":", 1)[1].split()) - allowed:
                problems.append(f"debian sources: {line.strip()} names a component outside {sorted(allowed)}")
    installed = {f["Package"] for f in packages}
    for component in record["components"]:
        if component.get("kind") == "dpkg":
            for name in component["packages"]:
                if name not in installed:
                    problems.append(f"component {component['id']}: package {name} is not installed")
    return problems


def spdx_header_problems(component, root, files):
    suffixes = tuple(component.get("spdx_headers", []))
    if not suffixes:
        return []
    wanted = set(spdx_ids(component["spdx"]))
    problems = []
    for rel in files:
        if not rel.endswith(suffixes):
            continue
        head = (read_text(Path(root) / rel) or "").splitlines()[:HEADER_LINES]
        found = [m.group(1) for line in head for m in [re.search(SPDX_TAG + r"\s*(\S+)", line)] if m]
        if not found:
            problems.append(f"{rel}: no SPDX licence header (component {component['id']})")
        elif not set(found) <= wanted:
            problems.append(f"{rel}: header {found[0]} is not among {sorted(wanted)} (component {component['id']})")
    return problems


def lock_problems(record, component, root):
    lock = load_lock(root, component)
    if isinstance(lock, str):
        return [lock]
    problems = []
    for entry in lock:
        name = entry.get("name", "?")
        licence = entry.get("licence") or {}
        if not licence.get("spdx") or not licence.get("holder") or not licence.get("source"):
            problems.append(f"fixture {name}: licence record lacks spdx, holder or source")
            continue
        problems.extend(licence_problems(record, licence["spdx"], f"fixture {name}"))
        entry_licence = record["licences"].get(licence["spdx"], {})
        if entry_licence.get("attribution_required") and not licence.get("attribution"):
            problems.append(f"fixture {name}: {licence['spdx']} requires an attribution text")
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            problems.append(f"fixture {name}: sha256 is not pinned")
    return problems


def load_lock(root, component):
    path = Path(root) / component["lock"]
    text = read_text(path)
    if text is None:
        return f"component {component['id']}: lock {component['lock']} is missing"
    try:
        entries = json.loads(text)["fixtures"]
    except (ValueError, KeyError, TypeError):
        return f"component {component['id']}: lock {component['lock']} is not a fixtures lock"
    return entries


def text_problems(record, root):
    """Every licence in use ships its text inside the image."""
    problems = []
    used = set()
    for component in record["components"]:
        used.update(spdx_ids(str(component.get("spdx", ""))))
    for ident in sorted(used):
        text = record["licences"].get(ident, {}).get("text")
        if text and not (Path(root) / text).is_file():
            problems.append(f"licence {ident}: text {text} is missing from the image")
    return problems


def check_tree(record, root, args):
    root = Path(root)
    problems = record_problems(record)
    if problems:
        return problems
    if not SHA_RE.match(args.commit or ""):
        return ["--commit must be a 40-digit hex commit"]
    packages = dpkg_packages(root)
    if packages is None:
        return ["var/lib/dpkg/status is missing: not a Debian image tree"]
    aliases = usr_prefix(root)
    owned = dpkg_owned(root, packages, aliases)
    claims = Claims(record)
    claimed = {c["id"]: [] for c in record["components"]}
    unrecorded = []
    for rel in walk_tree(root):
        kind, who = claims.owner(canonical(rel, aliases))
        if kind == "component":
            claimed[who["id"]].append(rel)
        elif kind is None and canonical(rel, aliases) not in owned:
            unrecorded.append(rel)
    problems.extend(f"unrecorded file: {rel}" for rel in unrecorded)
    problems.extend(debian_problems(record, root, packages, aliases))
    for component in record["components"]:
        kind = component.get("kind", "files")
        if kind in ("files", "lock") and not claimed[component["id"]] and not component.get("optional"):
            problems.append(f"component {component['id']}: no file in the image matches its paths (stale record)")
        problems.extend(spdx_header_problems(component, root, claimed[component["id"]]))
        if kind == "lock":
            problems.extend(lock_problems(record, component, root))
    problems.extend(text_problems(record, root))
    problems.extend(notices_problems(record, root, args, packages))
    return problems


def notices_problems(record, root, args, packages):
    path = Path(root) / record["notices_path"]
    current = read_text(path)
    if current is None:
        return [f"notices file {record['notices_path']} is missing"]
    if current != render_notices(record, root, args, packages):
        return [f"notices file {record['notices_path']} is stale: regenerate it with licensing.py notices"]
    return []


# --- notices -----------------------------------------------------------------


def component_lines(component, commit):
    version = f"(commit {commit})" if component['version'] == "@commit" else component['version']
    lines = [f"[{component['id']}] {component['name']} {version}", f"    licence: {component['spdx']}"]
    source = component.get("source_in") or component["source"]
    lines.append(f"    source: {source}")
    if component.get("embedded_in"):
        lines.append(f"    compiled into: {component['embedded_in']}")
    for path in component.get("paths", []):
        lines.append(f"    files: /{path}")
    for package in component.get("packages", []):
        lines.append(f"    debian package: {package}")
    for note in component.get("notes", []):
        lines.append(f"    note: {note}")
    return lines


def fixture_lines(record, root):
    lines = []
    for component in record["components"]:
        if component.get("kind") != "lock":
            continue
        entries = load_lock(root, component)
        if isinstance(entries, str):
            continue
        for entry in entries:
            licence = entry.get("licence") or {}
            lines.append(f"    {entry.get('name')}: {licence.get('spdx')}, {licence.get('holder')}")
            lines.append(f"        source: {licence.get('source')}")
            if licence.get("attribution"):
                lines.append(f"        attribution: {licence['attribution']}")
    return lines


def debian_lines(root, packages):
    lines = []
    for fields in packages:
        name = fields["Package"]
        path = copyright_path(root, name)
        declared = declared_licences(path) if path else []
        shown = ", ".join(declared) if declared else "see the copyright file"
        lines.append(f"    {name} {fields.get('Version', '?')} [{fields.get('Section', 'main')}]: {shown}")
        lines.append(f"        copyright: /usr/share/doc/{name}/copyright")
    return lines


def render_notices(record, root, args, packages):
    out = [
        "Pelorus tester image: third-party notices",
        "=========================================",
        "",
        "Pelorus source (EUPL-1.2 Article 5): " + f"{record['repository']} at commit {args.commit}",
        f"FFmpeg source: {args.ffmpeg_remote} at commit {args.ffmpeg_commit}, with the patch stack",
        "    ffmpeg-patches/ of the Pelorus commit above applied in series.txt order.",
        "Corresponding source of the GPL-3.0-or-later FFmpeg and of the Debian packages: the companion",
        "    image of the same registry package whose tag ends in -source (FFmpeg tree as compiled,",
        "    configure line, Debian source packages at the installed versions).",
        "This file is generated by tools/tester/licensing.py from tools/tester/licensing.json.",
        "",
        "Components",
        "----------",
    ]
    for component in record["components"]:
        out.extend(component_lines(component, args.commit))
        out.append("")
    out.extend([f"Debian packages ({len(packages)})", "-" * 22])
    out.extend(debian_lines(root, packages))
    fixtures = fixture_lines(record, root)
    if fixtures:
        out.extend(["", "Test fixtures (licence recorded in fixtures.lock.json)", "-" * 53])
        out.extend(fixtures)
    out.extend(["", "Licence texts shipped in the image", "-" * 34])
    used = set()
    for component in record["components"]:
        used.update(spdx_ids(str(component["spdx"])))
    for ident in sorted(used):
        text = record["licences"].get(ident, {}).get("text")
        out.append(f"    {ident}: " + (f"/{text}" if text else "no text file (see the component's source)"))
    return "\n".join(out) + "\n"


def cmd_notices(args):
    record = load_record(args.record)
    problems = record_problems(record)
    packages = dpkg_packages(args.root)
    if problems or packages is None or not SHA_RE.match(args.commit or ""):
        return report(problems or ["need a Debian tree and a 40-digit --commit"], "notices")
    target = Path(args.root) / record["notices_path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_notices(record, args.root, args, packages), encoding="utf-8")
    print(f"licensing: wrote /{record['notices_path']}")
    return 0


def cmd_check(args):
    record = load_record(args.record)
    return report(check_tree(record, args.root, args), "image tree")


def report(problems, what):
    for problem in problems:
        print(f"licensing: {problem}", file=sys.stderr)
    if problems:
        print(f"licensing: {what} refused ({len(problems)} problem(s))", file=sys.stderr)
        return 1
    print(f"licensing: {what} accepted")
    return 0


# --- self-test ---------------------------------------------------------------


def write(root, rel, text="x\n"):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def remove_tree(root):
    for base, dirs, files in os.walk(root, topdown=False):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(base, d))]:
            os.unlink(os.path.join(base, name))
        for name in dirs:
            if not os.path.islink(os.path.join(base, name)):
                os.rmdir(os.path.join(base, name))
    os.rmdir(root)


def fake_record():
    return {
        "schema_version": 1,
        "repository": "https://example.invalid/repo",
        "notices_path": "usr/share/licenses/t/NOTICES.txt",
        "licences": {
            "EUPL-1.2": {"redistributable": True, "text": "usr/share/licenses/t/EUPL-1.2.txt"},
            "MIT": {"redistributable": True, "text": None},
            "CC-BY-3.0": {"redistributable": True, "text": None, "attribution_required": True},
            "LicenseRef-nonfree": {"redistributable": False, "text": None},
        },
        "debian": {"components": ["main"]},
        "ignore": [{"paths": ["var/lib/dpkg/**"], "why": "package manager state", "expires": "2999-01-01"}],
        "components": [
            {"id": "own", "name": "own", "version": "@commit", "spdx": "EUPL-1.2", "paths": ["opt/own/*.py"],
             "source": "https://example.invalid/repo", "redistributable": True, "spdx_headers": [".py"]},
            {"id": "texts", "name": "texts", "version": "1", "spdx": "MIT",
             "paths": ["usr/share/licenses/t/**"], "source": "https://example.invalid/repo",
             "redistributable": True},
            {"id": "fixtures", "name": "fixtures", "version": "1", "spdx": "MIT", "kind": "lock",
             "paths": ["opt/own/fixtures.lock.json"], "lock": "opt/own/fixtures.lock.json",
             "source": "https://example.invalid/repo", "redistributable": True},
        ],
    }


def fake_tree(root, record):
    status = "Package: libfoo1\nStatus: install ok installed\nVersion: 1\nSection: libs\nArchitecture: amd64\n"
    write(root, "var/lib/dpkg/status", status + "\n")
    write(root, "var/lib/dpkg/info/libfoo1.list", "/usr/lib/libfoo.so.1\n/usr/share/doc/libfoo1/copyright\n")
    write(root, "usr/lib/libfoo.so.1")
    write(root, "usr/share/doc/libfoo1/copyright", "Format: dep5\n\nLicense: MIT\n")
    write(root, "opt/own/tool.py", f"# {SPDX_TAG} EUPL-1.2\n")
    lock = {"fixtures": [{"name": "f", "sha256": "a" * 64, "licence": {
        "spdx": "CC-BY-3.0", "holder": "h", "source": "https://example.invalid", "attribution": "credit"}}]}
    write(root, "opt/own/fixtures.lock.json", json.dumps(lock))
    write(root, "usr/share/licenses/t/EUPL-1.2.txt")
    args = argparse.Namespace(commit="a" * 40, ffmpeg_remote="https://example.invalid/ff", ffmpeg_commit="b" * 40)
    packages = dpkg_packages(root)
    write(root, record["notices_path"], render_notices(record, root, args, packages))
    return args


def regenerate(root, record, args):
    write(root, record["notices_path"], render_notices(record, root, args, dpkg_packages(root)))


def plant_file(root, record, args):
    write(root, "opt/own/stray.bin")

def unknown_licence(root, record, args):
    record["components"][0]["spdx"] = "Proprietary-X"

def refused_licence(root, record, args):
    record["components"][0]["spdx"] = "LicenseRef-nonfree"

def no_copyright(root, record, args):
    os.unlink(Path(root) / "usr/share/doc/libfoo1/copyright")

def copied_library(root, record, args):
    write(root, "usr/lib/libcopied.so.1")

def nonfree_package(root, record, args):
    write(root, "var/lib/dpkg/status", (Path(root) / "var/lib/dpkg/status").read_text().replace("Section: libs", "Section: non-free/libs"))

def stale_notices(root, record, args):
    record["components"][1]["version"] = "2"

def missing_text(root, record, args):
    os.unlink(Path(root) / "usr/share/licenses/t/EUPL-1.2.txt")

def stale_component(root, record, args):
    record["components"].append({"id": "gone", "name": "gone", "version": "1", "spdx": "MIT",
                                 "paths": ["opt/gone/**"], "source": "https://example.invalid",
                                 "redistributable": True})

def bad_header(root, record, args):
    write(root, "opt/own/tool.py", f"# {SPDX_TAG} GPL-2.0-only\n")

def not_redistributable(root, record, args):
    record["components"][0]["redistributable"] = False

def expired_ignore(root, record, args):
    record["ignore"][0]["expires"] = "2020-01-01"

def undated_ignore(root, record, args):
    del record["ignore"][0]["expires"]

def unattributed_fixture(root, record, args):
    lock = json.loads((Path(root) / "opt/own/fixtures.lock.json").read_text())
    lock["fixtures"][0]["licence"]["attribution"] = ""
    write(root, "opt/own/fixtures.lock.json", json.dumps(lock))


SELF_TEST_CASES = [
    ("unrecorded file", plant_file, "unrecorded file: opt/own/stray.bin", False),
    ("unknown licence", unknown_licence, "not in the record's licence registry", True),
    ("non-redistributable licence", refused_licence, "is not redistributable", True),
    ("non-redistributable component", not_redistributable, "redistributable is not true", True),
    ("debian package without copyright", no_copyright, "libfoo1/copyright is missing", False),
    ("library copied without its package", copied_library, "unrecorded file: usr/lib/libcopied.so.1", False),
    ("package outside main", nonfree_package, "archive component non-free is not permitted", True),
    ("stale notices", stale_notices, "is stale", False),
    ("licence text missing", missing_text, "text usr/share/licenses/t/EUPL-1.2.txt is missing", False),
    ("record entry without files", stale_component, "stale record", True),
    ("wrong SPDX header", bad_header, "is not among", True),
    ("fixture without attribution", unattributed_fixture, "requires an attribution text", True),
    ("expired ignore rule", expired_ignore, "expired on 2020-01-01", True),
    ("ignore rule without expiry", undated_ignore, "needs paths, why and expires", True),
]


def self_test_cases():
    return SELF_TEST_CASES


def run_case(base, index, mutate, regen):
    root = Path(base) / f"case{index}"
    root.mkdir()
    record = fake_record()
    args = fake_tree(root, record)
    mutate(root, record, args)
    if regen:
        regenerate(root, record, args)
    return check_tree(record, root, args)


def cmd_self_test(_args):
    base = Path(f"/tmp/licensing-self-test-{os.getpid()}")
    base.mkdir()
    failures = []
    try:
        good = base / "good"
        good.mkdir()
        record = fake_record()
        args = fake_tree(good, record)
        problems = check_tree(record, good, args)
        if problems:
            failures.append(f"the clean tree was refused: {problems}")
        for index, (name, mutate, expected, regen) in enumerate(self_test_cases()):
            problems = run_case(base, index, mutate, regen)
            if not any(expected in p for p in problems):
                failures.append(f"{name}: expected '{expected}', got {problems}")
    finally:
        remove_tree(base)
    return report(failures, "self-test") if failures else self_test_ok()


def self_test_ok():
    print(f"licensing: self-test accepted the clean tree and refused all {len(self_test_cases())} planted defects")
    return 0


def parser():
    top = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = top.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--record", default=str(RECORD))
    rec.add_argument("--repo", default=str(HERE.parent.parent))
    rec.set_defaults(run=cmd_record)
    for name, run in (("check", cmd_check), ("notices", cmd_notices)):
        p = sub.add_parser(name)
        p.add_argument("--record", default=str(RECORD))
        p.add_argument("--root", default="/")
        p.add_argument("--commit", required=True)
        p.add_argument("--ffmpeg-remote", required=True)
        p.add_argument("--ffmpeg-commit", required=True)
        p.set_defaults(run=run)
    sub.add_parser("self-test").set_defaults(run=cmd_self_test)
    return top


def main(argv):
    args = parser().parse_args(argv)
    try:
        return args.run(args)
    except RecordError as err:
        print(f"licensing: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
