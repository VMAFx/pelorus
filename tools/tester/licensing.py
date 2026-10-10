#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Licence record gate of the tester image (ADR-0178, #236) and the dev image (ADR-0179, #256).

`licensing.json` records every component a tester image ships: name, version,
SPDX licence, the paths it owns in the image, where its source is, and whether
it may be redistributed. This program checks a finished image tree against
that record and writes the notices file that travels inside the image.

    licensing.py record      [--record FILE] [--repo DIR]
    licensing.py check       [--kit KIT] --root DIR --commit SHA [--ffmpeg-remote URL --ffmpeg-commit SHA] [--record FILE]
    licensing.py notices     [--kit KIT] --root DIR --commit SHA [--ffmpeg-remote URL --ffmpeg-commit SHA] [--record FILE]
    licensing.py self-test

`check` exits 1 when a file belongs to no component, a licence is unknown or
not redistributable, a Debian package has no copyright file or comes from
outside the permitted archive components, a licence text is missing, a record
component matches no file, the notices file is absent or stale, or the tree
holds a forbidden file or package (an NVIDIA driver library) whoever owns it.
Every image kit (ADR-0180) is checked against the components that name it, or
name no kit. Only Debian `main` is permitted, with one exception: a `dpkg`
component that names `archive_component` (for example `non-free`) and
`archive_reason` admits exactly its own packages from that area, and only with a
redistributable licence; any other package outside `main` fails (ADR-0180
decision 4a: Debian's intel-media-va-driver-non-free is Expat, in `non-free`
because its GPU kernels come without source). `record`
checks the record itself and its version pins against the repository and
needs no image. `self-test` plants each defect in a fake tree and requires the
check to refuse it; the Containerfile runs it before the real check.

Every image has its own record. A record without a `notices` object writes the
tester image's header, which names the FFmpeg source and so needs the two
--ffmpeg-* arguments; a record with one (title and intro lines, `{repository}`
and `{commit}` filled in) writes that header instead. A component may name
`licence_files`: texts that must exist in the image beside its files, whatever
the licence registry says.

It needs only the standard library of the image's Debian python3 and runs on
every platform the fast suite covers. Run it as `python3 -I -B`.
"""

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
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
ARCHIVE_AREAS = frozenset({"contrib", "non-free", "non-free-firmware"})
USR_ALIASES = ("bin", "sbin", "lib", "lib32", "lib64", "libx32")
SPDX_OPERATORS = frozenset({"AND", "OR", "WITH"})
HEADER_LINES = 25
MAX_FILES = 2_000_000
MAX_LINK_DEPTH = 40
REQUIRED_COMPONENT_KEYS = ("id", "name", "version", "spdx", "source", "redistributable")
MAX_FORBIDDEN_HITS = 50
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
        rel_base = Path(os.path.relpath(base, root)).as_posix()
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
    problems.extend(archive_component_problems(component))
    if kind in ("files", "lock") and not component.get("paths"):
        problems.append(f"component {cid}: kind {kind} needs paths")
    if kind == "embedded" and not component.get("embedded_in"):
        problems.append(f"component {cid}: kind embedded needs embedded_in")
    if kind == "lock" and not component.get("lock"):
        problems.append(f"component {cid}: kind lock needs lock")
    return problems


def archive_component_problems(component):
    """A component outside Debian main is a dpkg component that says which area and why."""
    area = component.get("archive_component")
    cid = component.get("id", "?")
    if area is None:
        return [f"component {cid}: archive_reason needs archive_component"] if "archive_reason" in component else []
    problems = []
    if area not in ARCHIVE_AREAS:
        problems.append(f"component {cid}: archive_component must be one of {sorted(ARCHIVE_AREAS)}")
    if component.get("kind") != "dpkg":
        problems.append(f"component {cid}: archive_component needs kind dpkg")
    if not str(component.get("archive_reason", "")).strip():
        problems.append(f"component {cid}: archive_component needs archive_reason")
    return problems


def kit_problems(record):
    """Kits named by components exist; every forbidden rule names what and why.

    A record without `kits` describes one image; a record with them needs
    `forbidden` too (the vendor images, ADR-0180)."""
    kits = record.get("kits")
    if kits is None:
        problems = [f"component {c.get('id')}: names kits but the record has none"
                    for c in record["components"] if "kits" in c]
        return problems + (forbidden_rule_problems(record["forbidden"]) if "forbidden" in record else [])
    if not isinstance(kits, list) or not kits or not all(COMPONENT_ID_RE.match(str(k)) for k in kits):
        return ["record: kits must be a non-empty list of kit names"]
    problems = [f"component {c.get('id')}: kit {k} is not in the record's kits"
                for c in record["components"] for k in c.get("kits", []) if k not in kits]
    if "forbidden" not in record:
        return problems + ["record: a record with kits needs forbidden files and packages"]
    return problems + forbidden_rule_problems(record["forbidden"])


def forbidden_rule_problems(forbidden):
    problems = []
    for kind, key in (("files", "paths"), ("packages", "names")):
        rules = forbidden.get(kind)
        if not rules:
            problems.append(f"record: forbidden.{kind} needs at least one rule")
        for rule in rules or []:
            if not rule.get(key) or not rule.get("why"):
                problems.append(f"record: every forbidden.{kind} rule needs {key} and why")
    return problems


def kit_selection_problems(record, kit):
    """--kit is required by a record with kits and refused by one without."""
    kits = record.get("kits")
    if kits is None:
        return [f"--kit {kit}: this record has no kits"] if kit else []
    if kit is None:
        return [f"--kit is required: this record has kits {kits}"]
    return [] if kit in kits else [f"--kit {kit} is not one of the record's kits {kits}"]


def scoped(record, kit):
    """The record as one kit sees it: components that name the kit or name none."""
    copy = dict(record)
    copy["components"] = [c for c in record["components"] if kit in c.get("kits", [kit])]
    return copy


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
    problems.extend(kit_problems(record))
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
    """Versions that the repository pins elsewhere (KEY=value, or ARG KEY=value) must match the record."""
    problems = []
    for component in record["components"]:
        pin = component.get("version_from")
        if not pin:
            continue
        name, _, key = pin.partition(":")
        text = read_text(Path(repo) / name)
        match = re.search(rf"^(?:ARG )?{re.escape(key)}=(\S+)$", text or "", re.MULTILINE)
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


def forbidden_problems(record, files, packages, aliases):
    """Forbidden files and packages fail whoever owns them (ADR-0180)."""
    rules = record.get("forbidden", {})
    problems = []
    for rule in rules.get("files", []):
        compiled = compile_globs(rule["paths"])
        problems.extend(f"forbidden file: {rel} ({rule['why']})"
                        for rel in files if matches(compiled, canonical(rel, aliases)))
    for rule in rules.get("packages", []):
        compiled = compile_globs(rule["names"])
        problems.extend(f"forbidden package: {f['Package']} ({rule['why']})"
                        for f in packages if matches(compiled, f["Package"]))
    return problems[:MAX_FORBIDDEN_HITS]


def recorded_areas(record):
    """{archive area: package names} the record's components admit outside Debian main."""
    areas = {}
    for component in record["components"]:
        if component.get("archive_component") and component.get("kind") == "dpkg":
            areas.setdefault(component["archive_component"], set()).update(component["packages"])
    return areas


def debian_problems(record, root, packages, aliases):
    """Debian main only, plus the packages a redistributable component records from another area."""
    problems = []
    policy = record["debian"]
    allowed = set(policy.get("components", ["main"]))
    recorded = recorded_areas(record)
    for fields in packages:
        name = fields["Package"]
        section = fields.get("Section", "")
        component = section.split("/", 1)[0] if "/" in section else "main"
        if component not in allowed and name not in recorded.get(component, ()):
            problems.append(f"debian package {name}: archive component {component} is not permitted ({sorted(allowed)})")
        if copyright_path(root, name) is None:
            problems.append(f"debian package {name}: usr/share/doc/{name}/copyright is missing or empty")
    sources = read_text(Path(root) / policy.get("sources_file", "")) if policy.get("sources_file") else None
    if sources is not None:
        for line in sources.splitlines():
            if line.startswith("Components:") and set(line.split(":", 1)[1].split()) - allowed - set(recorded):
                problems.append(f"debian sources: {line.strip()} names a component outside {sorted(allowed | set(recorded))}")
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
    for component in record["components"]:
        for text in component.get("licence_files", []):
            if not (Path(root) / text).is_file():
                problems.append(f"component {component['id']}: licence file {text} is missing from the image")
    return problems


def check_tree(record, root, args):
    root = Path(root)
    problems = record_problems(record)
    if problems:
        return problems
    if not SHA_RE.match(args.commit or ""):
        return ["--commit must be a 40-digit hex commit"]
    problems = kit_selection_problems(record, args.kit)
    if problems:
        return problems
    packages = dpkg_packages(root)
    if packages is None:
        return ["var/lib/dpkg/status is missing: not a Debian image tree"]
    aliases = usr_prefix(root)
    owned = dpkg_owned(root, packages, aliases)
    files = walk_tree(root)
    problems.extend(forbidden_problems(record, files, packages, aliases))
    record = scoped(record, args.kit)
    claims = Claims(record)
    claimed = {c["id"]: [] for c in record["components"]}
    unrecorded = []
    for rel in files:
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
    for path in component.get("licence_files", []):
        lines.append(f"    licence text: /{path}")
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


def notices_head(record, args):
    custom = record.get("notices")
    if custom:
        fill = {"repository": record["repository"], "commit": args.commit}
        return [custom["title"], "=" * len(custom["title"]), ""] + [line.format(**fill) for line in custom["intro"]]
    if not (args.ffmpeg_remote and args.ffmpeg_commit):
        raise RecordError("this record has no notices header: pass --ffmpeg-remote and --ffmpeg-commit")
    return [
        "Pelorus tester image: third-party notices",
        "=========================================",
        "",
        "Pelorus source (EUPL-1.2 Article 5): " + f"{record['repository']} at commit {args.commit}",
        f"FFmpeg source: {args.ffmpeg_remote} at commit {args.ffmpeg_commit}, with the shared FFmpeg",
        "    fix series pinned in build-config.env of the Pelorus commit above (FFMPEG_SERIES_*) applied",
        "    first, then the patch stack ffmpeg-patches/ of that commit, each in its series.txt order.",
        "Corresponding source of the GPL-3.0-or-later FFmpeg and of the Debian packages: the companion",
        "    image of the same registry package whose tag ends in -source (FFmpeg tree as compiled,",
        "    configure line, Debian source packages at the installed versions).",
        "This file is generated by tools/tester/licensing.py from tools/tester/licensing.json.",
    ]


def render_notices(record, root, args, packages):
    out = notices_head(record, args)
    out += [
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
    problems = problems or kit_selection_problems(record, args.kit)
    if problems or packages is None or not SHA_RE.match(args.commit or ""):
        return report(problems or ["need a Debian tree and a 40-digit --commit"], "notices")
    for rel in write_notices(record, args.root, args, packages):
        print(f"licensing: wrote /{rel}")
    return 0


def write_notices(record, root, args, packages):
    """Write the notices file; return the paths written."""
    view = scoped(record, args.kit)
    written = [(record["notices_path"], render_notices(view, root, args, packages))]
    for rel, text in written:
        target = Path(root) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return [rel for rel, _ in written]


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
        "debian": {"components": ["main"], "sources_file": "etc/apt/sources.list.d/debian.sources"},
        "kits": ["generic", "nvidia", "intel"],
        "forbidden": {
            "files": [{"paths": ["**/libnvidia-*.so*", "**/nvidia_icd.json"], "why": "host driver files"}],
            "packages": [{"names": ["libnvidia-*"], "why": "host driver packages"}],
        },
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
            {"id": "nv-notice", "name": "nv notice", "version": "1", "spdx": "MIT", "kits": ["nvidia"],
             "paths": ["opt/nv/NOTICE.txt"], "source": "https://example.invalid/nv", "redistributable": True},
            {"id": "nonfree-driver", "name": "driver in non-free", "version": "1", "spdx": "MIT",
             "kind": "dpkg", "packages": ["media-driver-nf"], "kits": ["intel"],
             "archive_component": "non-free", "archive_reason": "GPU kernels come without source",
             "source": "https://example.invalid/nf", "redistributable": True},
        ],
    }


def fake_tree(root, record):
    status = "Package: libfoo1\nStatus: install ok installed\nVersion: 1\nSection: libs\nArchitecture: amd64\n"
    write(root, "var/lib/dpkg/status", status + "\n")
    write(root, "etc/apt/sources.list.d/debian.sources", "Types: deb\nComponents: main\n")
    write(root, "var/lib/dpkg/info/libfoo1.list", "/usr/lib/libfoo.so.1\n/usr/share/doc/libfoo1/copyright\n/etc/apt/sources.list.d/debian.sources\n")
    write(root, "usr/lib/libfoo.so.1")
    write(root, "usr/share/doc/libfoo1/copyright", "Format: dep5\n\nLicense: MIT\n")
    write(root, "opt/own/tool.py", f"# {SPDX_TAG} EUPL-1.2\n")
    lock = {"fixtures": [{"name": "f", "sha256": "a" * 64, "licence": {
        "spdx": "CC-BY-3.0", "holder": "h", "source": "https://example.invalid", "attribution": "credit"}}]}
    write(root, "opt/own/fixtures.lock.json", json.dumps(lock))
    write(root, "usr/share/licenses/t/EUPL-1.2.txt")
    args = argparse.Namespace(commit="a" * 40, ffmpeg_remote="https://example.invalid/ff", ffmpeg_commit="b" * 40,
                              kit="generic")
    regenerate(root, record, args)
    return args


def regenerate(root, record, args):
    write_notices(record, root, args, dpkg_packages(root))


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

def missing_licence_file(root, record, args):
    record["components"][1]["licence_files"] = ["usr/share/licenses/t/MIT-notice.txt"]

def planted_nvidia_library(root, record, args):
    write(root, "usr/lib/libnvidia-encode.so.1")
    with open(Path(root) / "var/lib/dpkg/info/libfoo1.list", "a", encoding="utf-8") as fh:
        fh.write("/usr/lib/libnvidia-encode.so.1\n")

def planted_nvidia_icd(root, record, args):
    write(root, "opt/own/nvidia_icd.json")
    record["components"][0]["paths"].append("opt/own/*.json")

def add_package(root, name, section):
    status = Path(root) / "var/lib/dpkg/status"
    status.write_text(status.read_text() + f"Package: {name}\nStatus: install ok installed\nVersion: 1\n"
                      f"Section: {section}\nArchitecture: amd64\n\n")
    write(root, f"usr/share/doc/{name}/copyright", "Format: dep5\n\nLicense: MIT\n")
    write(root, f"var/lib/dpkg/info/{name}.list", f"/usr/share/doc/{name}/copyright\n")

def nvidia_package(root, record, args):
    add_package(root, "libnvidia-encode1", "libs")

def other_kit_files_missing(root, record, args):
    args.kit = "nvidia"

def unknown_kit(root, record, args):
    args.kit = "amd"

def component_unknown_kit(root, record, args):
    record["components"][3]["kits"] = ["amd"]

def no_forbidden_rules(root, record, args):
    record["forbidden"]["files"] = []

def no_kit_given(root, record, args):
    args.kit = None

def kits_without_forbidden(root, record, args):
    del record["forbidden"]

def intel_tree(root, record, args):
    """The clean tree of kit intel: the recorded driver from non-free, its source area enabled."""
    add_package(root, "media-driver-nf", "non-free/video")
    write(root, "etc/apt/sources.list.d/debian.sources", "Types: deb\nComponents: main non-free\n")
    args.kit = "intel"
    regenerate(root, record, args)

def driver_outside_its_kit(root, record, args):
    add_package(root, "media-driver-nf", "non-free/video")

def other_package_beside_driver(root, record, args):
    intel_tree(root, record, args)
    add_package(root, "other-nf", "non-free/libs")
    regenerate(root, record, args)

def contrib_beside_driver(root, record, args):
    intel_tree(root, record, args)
    add_package(root, "other-contrib", "contrib/libs")
    regenerate(root, record, args)

def unrecorded_source_area(root, record, args):
    intel_tree(root, record, args)
    write(root, "etc/apt/sources.list.d/debian.sources", "Types: deb\nComponents: main non-free contrib\n")

def driver_not_redistributable(root, record, args):
    intel_tree(root, record, args)
    record["licences"]["LicenseRef-nonfree"] = {"redistributable": False, "text": None}
    record["components"][4]["spdx"] = "LicenseRef-nonfree"

def driver_without_reason(root, record, args):
    del record["components"][4]["archive_reason"]

def driver_in_unknown_area(root, record, args):
    record["components"][4]["archive_component"] = "restricted"

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
    ("component licence file missing", missing_licence_file, "licence file usr/share/licenses/t/MIT-notice.txt is missing", False),
    ("fixture without attribution", unattributed_fixture, "requires an attribution text", True),
    ("expired ignore rule", expired_ignore, "expired on 2020-01-01", True),
    ("ignore rule without expiry", undated_ignore, "needs paths, why and expires", True),
    ("NVIDIA library owned by a package", planted_nvidia_library, "forbidden file: usr/lib/libnvidia-encode.so.1", False),
    ("NVIDIA ICD claimed by a component", planted_nvidia_icd, "forbidden file: opt/own/nvidia_icd.json", True),
    ("NVIDIA driver package", nvidia_package, "forbidden package: libnvidia-encode1", False),
    ("kit component without its files", other_kit_files_missing, "component nv-notice: no file in the image", True),
    ("unknown kit", unknown_kit, "--kit amd is not one of the record's kits", False),
    ("component names an unknown kit", component_unknown_kit, "kit amd is not in the record's kits", False),
    ("forbidden file rules removed", no_forbidden_rules, "forbidden.files needs at least one rule", False),
    ("record with kits checked without --kit", no_kit_given, "--kit is required", False),
    ("record with kits but no forbidden rules", kits_without_forbidden, "a record with kits needs forbidden", False),
    ("recorded non-free package in a kit that does not record it", driver_outside_its_kit, "debian package media-driver-nf: archive component non-free is not permitted", False),
    ("other non-free package beside the recorded one", other_package_beside_driver, "debian package other-nf: archive component non-free is not permitted", False),
    ("contrib package beside the recorded non-free one", contrib_beside_driver, "debian package other-contrib: archive component contrib is not permitted", False),
    ("source area the record does not name", unrecorded_source_area, "names a component outside", False),
    ("non-free package with a non-redistributable licence", driver_not_redistributable, "is not redistributable", False),
    ("non-free component without a reason", driver_without_reason, "archive_component needs archive_reason", False),
    ("non-free component in an unknown archive area", driver_in_unknown_area, "archive_component must be one of", False),
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
    base = Path(tempfile.mkdtemp(prefix="licensing-self-test-"))
    failures = []
    try:
        good = base / "good"
        good.mkdir()
        record = fake_record()
        args = fake_tree(good, record)
        problems = check_tree(record, good, args)
        if problems:
            failures.append(f"the clean tree was refused: {problems}")
        intel = base / "intel"
        intel.mkdir()
        args = fake_tree(intel, record)
        intel_tree(intel, record, args)
        problems = check_tree(record, intel, args)
        if problems:
            failures.append(f"the clean tree of the kit with a recorded non-free package was refused: {problems}")
        for index, (name, mutate, expected, regen) in enumerate(self_test_cases()):
            problems = run_case(base, index, mutate, regen)
            if not any(expected in p for p in problems):
                failures.append(f"{name}: expected '{expected}', got {problems}")
    finally:
        remove_tree(base)
    return report(failures, "self-test") if failures else self_test_ok()


def self_test_ok():
    print(f"licensing: self-test accepted the clean trees (with a recorded non-free package) and refused all "
          f"{len(self_test_cases())} planted defects")
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
        p.add_argument("--kit", default=None, help="image kit of a record with kits: generic, nvidia or intel (ADR-0180)")
        p.add_argument("--record", default=str(RECORD))
        p.add_argument("--root", default="/")
        p.add_argument("--commit", required=True)
        p.add_argument("--ffmpeg-remote", default=None)
        p.add_argument("--ffmpeg-commit", default=None)
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
