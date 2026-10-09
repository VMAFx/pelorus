#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Pelorus tester fixtures (ADR-0173, #225).

Every fixture is pinned by SHA-256 in `fixtures.lock.json` and carries a
licence record. The tester runs offline: `verify` needs no network, `fetch`
is the only command that does and writes each fixture into the cache once.

    pelorus_tester_fixtures.py verify  [--cache DIR] [--lock FILE]
    pelorus_tester_fixtures.py fetch   [--cache DIR] [--lock FILE] [--ffmpeg BIN] [NAME ...]
    pelorus_tester_fixtures.py notices [--lock FILE]

Standard library only; run it as `python3 -I`. The report program imports the
functions here and runs `self_test` from its own `--self-test`.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

LOCK_PATH = Path(__file__).with_name("fixtures.lock.json")
RULES = ("fixture_hash", "fixture_licence", "fixture_attribution")
CHUNK = 1 << 20
MAX_FIXTURE_BYTES = 2 << 30
MAX_FIXTURES = 64
FETCH_TIMEOUT_S = 120
USER_AGENT = "pelorus-tester-fixtures/0.2 (+https://github.com/VMAFx/pelorus)"
GENERATE_TIMEOUT_S = 300
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SPDX_RE = re.compile(r"^[A-Za-z0-9.+-]{2,64}$")
URL_RE = re.compile(r"^https://[^\s]+$")


def default_cache():
    env = os.environ.get("PELORUS_TESTER_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "pelorus-tester"


def sha256_file(path):
    """Hash a file in bounded chunks; None when it cannot be read."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for _ in range(MAX_FIXTURE_BYTES // CHUNK + 1):
                block = fh.read(CHUNK)
                if not block:
                    return digest.hexdigest()
                digest.update(block)
    except OSError:
        return None
    return None


def load_lock(path=LOCK_PATH):
    """Return (entries, error text)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], "cannot read fixture lock %s: %s" % (path, exc)
    entries = data.get("fixtures") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries or len(entries) > MAX_FIXTURES:
        return [], "fixture lock needs 1..%d entries under 'fixtures'" % MAX_FIXTURES
    return entries, ""


def entry_shape_problems(entry):
    """Structural problems that make an entry unusable at all."""
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    name = entry.get("name", "")
    errs = []
    if not NAME_RE.match(str(name)):
        errs.append("bad or missing name %r" % (name,))
    if not SHA_RE.match(str(entry.get("sha256", ""))):
        errs.append("%s: sha256 must be 64 lowercase hex digits" % name)
    if Path(str(entry.get("file", ""))).name != entry.get("file") or not entry.get("file"):
        errs.append("%s: file must be a bare file name" % name)
    if "generate" not in entry and "download" not in entry:
        errs.append("%s: needs a 'generate' recipe or a 'download' source" % name)
    return errs


def licence_problems(entry, disabled=frozenset()):
    """The licence gate: a fixture without a complete licence record fails."""
    if "fixture_licence" in disabled:
        return []
    name = entry.get("name", "?")
    lic = entry.get("licence")
    if not isinstance(lic, dict):
        return ["%s: no licence record" % name]
    errs = []
    if not SPDX_RE.match(str(lic.get("spdx", ""))):
        errs.append("%s: licence.spdx missing or not an SPDX id" % name)
    if not str(lic.get("holder", "")).strip():
        errs.append("%s: licence.holder missing" % name)
    src = str(lic.get("source", "")).strip()
    if not src:
        errs.append("%s: licence.source missing" % name)
    errs.extend(attribution_problems(entry, lic, disabled))
    return errs


def attribution_problems(entry, lic, disabled):
    """CC-BY style licences need the attribution text, naming the holder."""
    if "fixture_attribution" in disabled:
        return []
    if not str(lic.get("spdx", "")).upper().startswith("CC-BY"):
        return []
    text = str(lic.get("attribution", ""))
    holder = str(lic.get("holder", ""))
    if not text.strip() or not holder or holder not in text:
        return ["%s: %s needs attribution text naming the holder"
                % (entry.get("name", "?"), lic.get("spdx"))]
    return []


def hash_problems(entry, path, disabled=frozenset()):
    """Check the cached bytes against the pin; the only data trusted is the lock."""
    name = entry.get("name", "?")
    if not Path(path).is_file():
        return ["%s: not in the cache (%s)" % (name, Path(path).name)]
    if "fixture_hash" in disabled:
        return []
    got = sha256_file(path)
    if got is None:
        return ["%s: cannot read %s" % (name, Path(path).name)]
    if got != entry.get("sha256"):
        return ["%s: sha256 mismatch (got %s, pinned %s)"
                % (name, got[:12], str(entry.get("sha256"))[:12])]
    return []


def verify_entry(entry, cache, disabled=frozenset()):
    errs = entry_shape_problems(entry)
    if errs:
        return errs
    errs = licence_problems(entry, disabled)
    return errs + hash_problems(entry, Path(cache) / entry["file"], disabled)


def verify_all(entries, cache, disabled=frozenset()):
    """Return (problems, verified names); offline, no generation."""
    problems, good = [], []
    for entry in entries[:MAX_FIXTURES]:
        errs = verify_entry(entry, cache, disabled)
        problems.extend(errs)
        if not errs:
            good.append(entry["name"])
    return problems, good


def render_notices(entries):
    """THIRD_PARTY_NOTICES fragment: one block per fixture with a licence."""
    lines = ["Tester fixtures", ""]
    for entry in entries[:MAX_FIXTURES]:
        lic = entry.get("licence", {})
        lines.append("- %s (%s): %s, %s" % (entry.get("name"), entry.get("file"),
                                            lic.get("spdx"), lic.get("holder")))
        lines.append("  source: %s" % lic.get("source"))
        if lic.get("attribution"):
            lines.append("  " + lic["attribution"])
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ materialising

def publish(tmp_path, entry, cache, disabled=frozenset()):
    """Verify a freshly built file against its pin, then move it into place."""
    errs = hash_problems(entry, tmp_path, disabled)
    if errs:
        Path(tmp_path).unlink(missing_ok=True)
        return errs
    try:
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, Path(cache) / entry["file"])
    except OSError as exc:
        return ["%s: cannot publish into the cache: %s" % (entry["name"], exc.strerror)]
    return []


def generate(entry, cache, ffmpeg, run):
    """Render a lavfi fixture from its recorded recipe; returns problems."""
    recipe = entry["generate"]["lavfi"]
    fd, tmp = tempfile.mkstemp(dir=str(cache), suffix=".part")
    os.close(fd)
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            recipe, "-vf", "format=" + entry["pixfmt"], "-frames:v",
            str(entry["frames"]), "-an", "-f", "rawvideo", tmp]
    code, text, err = run(argv, GENERATE_TIMEOUT_S)
    if code != 0:
        Path(tmp).unlink(missing_ok=True)
        return ["%s: lavfi generation failed: %s" % (entry["name"], err or text[-200:])]
    return publish(tmp, entry, cache)


def download(entry, cache, opener=urllib.request.urlopen):
    """Fetch the source file once; returns (path or None, problems)."""
    spec = entry["download"]
    if not URL_RE.match(spec.get("url", "")):
        return None, ["%s: download url must be https" % entry["name"]]
    dest = Path(cache) / ("src-" + entry["name"] + ".bin")
    pin = {"name": entry["name"], "sha256": spec.get("sha256")}
    if not hash_problems(pin, dest):
        return dest, []
    limit = int(spec.get("max_bytes", 1 << 26))
    fd, tmp = tempfile.mkstemp(dir=str(cache), suffix=".part")
    try:
        req = urllib.request.Request(spec["url"], headers={"User-Agent": USER_AGENT})
        with os.fdopen(fd, "wb") as out, opener(req, timeout=FETCH_TIMEOUT_S) as resp:
            body = resp.read(limit + 1)
            out.write(body[:limit])
    except OSError as exc:
        Path(tmp).unlink(missing_ok=True)
        return None, ["%s: download failed: %s" % (entry["name"], exc)]
    errs = hash_problems(pin, tmp)
    if errs or len(body) > limit:
        Path(tmp).unlink(missing_ok=True)
        return None, errs or ["%s: download larger than %d bytes" % (entry["name"], limit)]
    try:
        os.replace(tmp, dest)
    except OSError as exc:
        return None, ["%s: cannot store the download: %s" % (entry["name"], exc.strerror)]
    return dest, []


def extract(entry, source, cache, ffmpeg, run):
    spec = entry["download"]
    fd, tmp = tempfile.mkstemp(dir=str(cache), suffix=".part")
    os.close(fd)
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"] + list(spec["input_args"])
    argv += ["-i", str(source), "-vf", spec["filter"], "-frames:v",
             str(entry["frames"]), "-an", "-f", "rawvideo", tmp]
    code, text, err = run(argv, GENERATE_TIMEOUT_S)
    if code != 0:
        Path(tmp).unlink(missing_ok=True)
        return ["%s: extraction failed: %s" % (entry["name"], err or text[-200:])]
    return publish(tmp, entry, cache)


def fetch_entry(entry, cache, ffmpeg, run, offline=False):
    """Make one fixture present and verified in the cache; returns problems."""
    errs = entry_shape_problems(entry) or licence_problems(entry)
    if errs:
        return errs
    Path(cache).mkdir(parents=True, exist_ok=True)
    if not verify_entry(entry, cache):
        return []
    if "generate" in entry:
        return generate(entry, cache, ffmpeg, run)
    if offline:
        return ["%s: not in the cache and offline mode forbids a download" % entry["name"]]
    source, errs = download(entry, cache)
    return errs if source is None else extract(entry, source, cache, ffmpeg, run)


def ensure_offline(entry, cache, ffmpeg, run):
    """For a stage: use the cache; render lavfi fixtures, never download.

    Returns (path or None, problems, absent) where `absent` means the fixture is
    simply not baked into the cache (a reasoned not_run, not a failure)."""
    path = Path(cache) / entry["file"]
    present = path.is_file()
    if present:
        errs = verify_entry(entry, cache)
        return (path if not errs else None), errs, False
    errs = entry_shape_problems(entry) or licence_problems(entry)
    if errs:
        return None, errs, False
    if "generate" not in entry:
        return None, ["%s: not in the cache and the tester runs offline "
                      "(run `fetch` once)" % entry["name"]], True
    try:
        Path(cache).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, ["cannot create the fixture cache %s: %s (set PELORUS_TESTER_CACHE "
                      "to a writable directory)" % (cache, exc.strerror)], False
    errs = generate(entry, cache, ffmpeg, run)
    return (None if errs else path), errs, False


# --------------------------------------------------------------- self-test

def self_test(disabled=frozenset()):
    """Return failing check names; each rule has a planted bad fixture."""
    failures = []

    def expect(name, cond):
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        data = bytes(range(256)) * 16
        (Path(tmp) / "good.yuv").write_bytes(data)
        lic = {"spdx": "CC-BY-3.0", "holder": "Holder", "source": "src",
               "attribution": "Work by Holder, CC BY 3.0"}
        good = {"name": "good", "file": "good.yuv", "generate": {"lavfi": "x"},
                "sha256": hashlib.sha256(data).hexdigest(), "licence": lic}
        expect("fixture_good_verifies", not verify_entry(good, tmp, disabled))
        flipped = bytearray(data)
        flipped[100] ^= 1
        (Path(tmp) / "flip.yuv").write_bytes(bytes(flipped))
        bad = dict(good, name="flip", file="flip.yuv")
        expect("rejects_flipped_byte", bool(verify_entry(bad, tmp, disabled)))
        nolic = {k: v for k, v in good.items() if k != "licence"}
        expect("rejects_missing_licence", bool(verify_entry(nolic, tmp, disabled)))
        noattr = dict(good, licence=dict(lic, attribution=""))
        expect("rejects_missing_attribution", bool(verify_entry(noattr, tmp, disabled)))
        expect("rejects_absent_file", bool(verify_entry(dict(good, file="none.yuv"), tmp, disabled)))
        remote = {k: v for k, v in good.items() if k != "generate"}
        remote.update(file="none.yuv", download={"url": "https://example.invalid/x"})
        expect("offline_download_absent", ensure_offline(remote, tmp, "ffmpeg", None)[2])
        blocked = ensure_offline(dict(good, file="new.yuv"), Path(tmp) / "good.yuv" / "cache",
                                 "ffmpeg", None)
        expect("unwritable_cache_is_named", blocked[0] is None and not blocked[2]
               and "cannot create the fixture cache" in blocked[1][0])
    entries, err = load_lock()
    expect("lock_loads", not err and bool(entries))
    expect("lock_entries_licensed", not [e for e in entries if licence_problems(e, disabled)])
    expect("notices_name_bbb", "Blender Foundation" in render_notices(entries)
           and "Creative Commons Attribution 3.0" in render_notices(entries))
    return failures


# --------------------------------------------------------------------- CLI

def make_runner():
    def run(argv, timeout_s):
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=timeout_s,
                                  stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired:
            return None, "", "timed out after %ss" % timeout_s
        except OSError as exc:
            return None, "", "cannot start %s: %s" % (argv[0], exc.strerror)
        return proc.returncode, proc.stderr.decode("utf-8", "replace"), ""
    return run


def cmd_verify(args):
    entries, err = load_lock(args.lock)
    if err:
        print("error: " + err, file=sys.stderr)
        return 2
    problems, good = verify_all(entries, Path(args.cache))
    for line in problems:
        print("FAIL " + line, file=sys.stderr)
    print("verified: %s" % (", ".join(good) or "none"))
    return 1 if problems else 0


def cmd_fetch(args):
    entries, err = load_lock(args.lock)
    if err:
        print("error: " + err, file=sys.stderr)
        return 2
    run, bad = make_runner(), 0
    for entry in entries[:MAX_FIXTURES]:
        if args.names and entry.get("name") not in args.names:
            continue
        errs = fetch_entry(entry, Path(args.cache), args.ffmpeg, run)
        bad += bool(errs)
        print("%-16s %s" % (entry.get("name"), "FAIL " + "; ".join(errs) if errs else "ready"))
    return 1 if bad else 0


def cmd_notices(args):
    entries, err = load_lock(args.lock)
    if err:
        print("error: " + err, file=sys.stderr)
        return 2
    sys.stdout.write(render_notices(entries))
    return 0


def main(argv):
    top = argparse.ArgumentParser(prog="pelorus_tester_fixtures.py",
                                  description=__doc__.split("\n")[0])
    sub = top.add_subparsers(dest="cmd", required=True)
    for name in ("verify", "fetch", "notices"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--lock", default=str(LOCK_PATH))
        if name != "notices":
            cmd.add_argument("--cache", default=str(default_cache()))
    sub.choices["fetch"].add_argument("--ffmpeg", default=os.environ.get("FFMPEG_BIN", "ffmpeg"))
    sub.choices["fetch"].add_argument("names", nargs="*")
    args = top.parse_args(argv)
    return {"verify": cmd_verify, "fetch": cmd_fetch, "notices": cmd_notices}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
