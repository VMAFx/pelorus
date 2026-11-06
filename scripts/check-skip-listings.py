#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Every skip a wired test can take is listed before any lane takes it.

scripts/check-test-skips.py (ADR-0188) fails a SKIP that .config/test-skips.json
does not list, but only on a lane where the skip happens. A new test that skips
when a tool is missing passes on a host that has the tool and first fails on a
CI lane without it. This check reads Meson's intro-tests.json, opens each
test's script under scripts/, and requires a test-skips.json entry for every
literal "SKIP: <reason>" line that an exit 77 follows within two lines.

Exit status: 0 every such skip is listed; 1 an unlisted skip; 2 the
introspection file or the allow-list is missing or malformed.

    python3 -I scripts/check-skip-listings.py build/meson-info/intro-tests.json
    python3 -I scripts/check-skip-listings.py --self-test
"""

import argparse
import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
ALLOW_LIST = ROOT / ".config" / "test-skips.json"
SKIP_LINE = re.compile(r"""(?:print\(|echo\s+)["'](SKIP: [^"'\\\n]+)["']""")
EXIT_77 = re.compile(r"\b(?:return|exit)\s*\(?\s*77\b|sys\.exit\(\s*77\s*\)")
LOOKAHEAD = 2
MAX_TESTS = 10000


def _load_check_test_skips():
    """Import check-test-skips.py by path to reuse its allow-list reader."""
    path = SCRIPTS / "check-test-skips.py"
    spec = importlib.util.spec_from_file_location("check_test_skips", path)
    if spec is None or spec.loader is None:
        raise ImportError("%s: cannot load" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SKIPS = _load_check_test_skips()


def skip_reasons(text):
    """Return the literal SKIP reasons in text that an exit 77 follows."""
    lines = text.splitlines()
    found = []
    for idx, line in enumerate(lines):
        match = SKIP_LINE.search(line)
        if match is None:
            continue
        tail = lines[idx + 1:idx + 1 + LOOKAHEAD]
        if any(EXIT_77.search(after) for after in tail):
            found.append(match.group(1))
    return found


def load_tests(path):
    """Return [(test name, [script paths under scripts/])] from intro-tests.json."""
    try:
        tests = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise _SKIPS.Malformed("%s: %s" % (path, err)) from err
    if not isinstance(tests, list) or not tests:
        raise _SKIPS.Malformed("%s: no tests" % path)
    out = []
    for test in tests[:MAX_TESTS]:
        if not isinstance(test, dict) or not test.get("suite") or "name" not in test:
            raise _SKIPS.Malformed("%s: malformed test entry" % path)
        project = test["suite"][0].split(":", 1)[0]
        scripts = [Path(c) for c in test.get("cmd", [])
                   if Path(c).is_relative_to(SCRIPTS) and Path(c).is_file()]
        out.append(("%s:%s" % (project, test["name"]), scripts))
    return out


def check(intro_path, allow_path):
    """Print each unlisted skip; return 0 when none, 1 otherwise."""
    allowed = _SKIPS.load_allow_list(allow_path)
    failures = 0
    for name, scripts in load_tests(intro_path):
        for script in scripts:
            for reason in skip_reasons(script.read_text(encoding="utf-8")):
                if allowed.get(name) != reason:
                    print("check-skip-listings: %s (%s) can skip with %r, which %s "
                          "does not list" % (name, script.relative_to(ROOT), reason,
                                             allow_path.name))
                    failures += 1
    return 1 if failures else 0


def self_test():
    """Planted unlisted, listed, wrong-reason and non-exit cases."""
    errors = []
    # The planted lines are assembled at run time so that this file's own
    # source holds no SKIP line the live check would count.
    plant = "%sSKIP" + ": %s\n"
    script = SCRIPTS / "test-semgrep-wall-clock.py"
    if skip_reasons(plant % ('    print("', 'tool not found")\n    return 77')) != ["SKIP: tool not found"]:
        errors.append("python print + return 77 not found")
    if skip_reasons(plant % ('echo "', 'x265 not found"\nexit 77')) != ["SKIP: x265 not found"]:
        errors.append("shell echo + exit 77 not found")
    if skip_reasons(plant % ('print("', 'numpy missing")\n\nrun()\n\nexit 77')) != []:
        errors.append("a SKIP print without exit 77 counted")
    with tempfile.TemporaryDirectory() as tmp:
        allow = Path(tmp) / "allow.json"
        intro = Path(tmp) / "intro-tests.json"
        intro.write_text(json.dumps([{"name": "planted", "suite": ["pelorus:fast"],
                                      "cmd": ["/usr/bin/python3", str(script)]}]),
                         encoding="utf-8")
        cases = [("unlisted", [], 1),
                 ("listed", [{"test": "pelorus:planted", "reason": "SKIP: semgrep not found",
                              "issue": "planted"}], 0),
                 ("wrong reason", [{"test": "pelorus:planted", "reason": "SKIP: other",
                                    "issue": "planted"}], 1)]
        for label, entries, want in cases:
            allow.write_text(json.dumps(entries), encoding="utf-8")
            got = check(intro, allow)
            if got != want:
                errors.append("%s: rc %d, want %d" % (label, got, want))
        intro.write_text("[]", encoding="utf-8")
        try:
            check(intro, allow)
            errors.append("empty intro-tests.json accepted")
        except _SKIPS.Malformed:
            pass
    for err in errors:
        print("check-skip-listings self-test: FAIL %s" % err)
    return 1 if errors else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run planted cases")
    parser.add_argument("intro", nargs="?", type=Path, help="build/meson-info/intro-tests.json")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.intro is None:
        parser.error("intro-tests.json path required")
    try:
        return check(args.intro, ALLOW_LIST)
    except _SKIPS.Malformed as err:
        print("check-skip-listings: %s" % err)
        return 2


if __name__ == "__main__":
    sys.exit(main())
