#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""A skipped Meson test passes only when it is named, with its reason (ADR-0188).

`meson test` exits 0 when a test exits 77 (SKIP), whatever the reason. A test
whose device, binary or input went missing therefore reported green. This
check reads the run's testlog.json and fails on every SKIP that
.config/test-skips.json does not list with the exact reason the test printed.

The allow-list holds objects {"test", "reason", "issue"}: "test" is the Meson
test name ("pelorus:<name>"), "reason" the test's last stdout line that starts
with "SKIP: ", "issue" the issue or ADR that keeps the skip. A listed test that
skips for another reason fails like an unlisted one.

Exit status: 0 every skip is listed; 1 an unlisted skip; 2 the log or the
allow-list is missing, empty or malformed (never a pass).

    python3 -I scripts/check-test-skips.py build/meson-logs/testlog.json
    python3 -I scripts/check-test-skips.py --self-test
"""

import json
import sys
import tempfile
from pathlib import Path

ALLOW_LIST = Path(__file__).resolve().parent.parent / ".config" / "test-skips.json"
SKIP_PREFIX = "SKIP: "
MAX_RECORDS = 10000


class Malformed(Exception):
    """An input file that cannot be judged."""


def load_allow_list(path):
    """Return {test name: reason line} from the allow-list file."""
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise Malformed("%s: %s" % (path, err)) from err
    if not isinstance(entries, list):
        raise Malformed("%s: top level is not a list" % path)
    allowed = {}
    for entry in entries[:MAX_RECORDS]:
        if not isinstance(entry, dict) or not all(
                isinstance(entry.get(key), str) and entry.get(key)
                for key in ("test", "reason", "issue")):
            raise Malformed("%s: entry needs test, reason and issue: %r" % (path, entry))
        if not entry["reason"].startswith(SKIP_PREFIX):
            raise Malformed("%s: reason must start with %r: %r"
                            % (path, SKIP_PREFIX, entry["reason"]))
        if entry["test"] in allowed:
            raise Malformed("%s: duplicate test %r" % (path, entry["test"]))
        allowed[entry["test"]] = entry["reason"]
    return allowed


def load_records(path):
    """Return the testlog.json records; a log without one is malformed."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as err:
        raise Malformed("%s: %s" % (path, err)) from err
    records = []
    for line in lines[:MAX_RECORDS]:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as err:
            raise Malformed("%s: %s" % (path, err)) from err
        if not isinstance(record, dict) or not isinstance(record.get("name"), str):
            raise Malformed("%s: record without a name" % path)
        records.append(record)
    if not records:
        raise Malformed("%s: no test record" % path)
    return records


def test_name(record):
    """Strip Meson's "<suite> - " prefix: "fast - pelorus:x" -> "pelorus:x"."""
    return record["name"].rpartition(" - ")[2]


def skip_reason(record):
    """The last stdout line that starts with SKIP_PREFIX, or ""."""
    lines = str(record.get("stdout") or "").splitlines()
    found = [line.strip() for line in lines if line.strip().startswith(SKIP_PREFIX)]
    return found[-1] if found else ""


def check(log_path, allow_path):
    """Return the exit status; prints every unlisted skip."""
    try:
        allowed = load_allow_list(allow_path)
        records = load_records(log_path)
    except Malformed as err:
        print("check-test-skips: malformed input: %s" % err)
        return 2
    status = 0
    for record in records:
        if record.get("result") != "SKIP":
            continue
        name, reason = test_name(record), skip_reason(record)
        if allowed.get(name) == reason:
            print("check-test-skips: %s skipped as listed: %s" % (name, reason))
            continue
        print("check-test-skips: %s skipped and is not listed with this reason"
              " in %s: %r" % (name, allow_path.name, reason or "(no SKIP line)"))
        status = 1
    return status


def record_line(name, result, stdout):
    return json.dumps({"name": "fast - " + name, "result": result, "stdout": stdout})


SELF_TEST_ALLOW = [{"test": "pelorus:a", "reason": "SKIP: no device", "issue": "#1"}]
SELF_TEST_CASES = [
    # (label, testlog lines, allow-list, expected status)
    ("listed skip", [record_line("pelorus:a", "SKIP", "x\nSKIP: no device\n")],
     SELF_TEST_ALLOW, 0),
    ("no skip", [record_line("pelorus:b", "OK", "")], SELF_TEST_ALLOW, 0),
    ("unlisted skip", [record_line("pelorus:b", "SKIP", "SKIP: no device\n")],
     SELF_TEST_ALLOW, 1),
    ("listed test, other reason",
     [record_line("pelorus:a", "SKIP", "SKIP: binary missing\n")], SELF_TEST_ALLOW, 1),
    ("listed test, no SKIP line", [record_line("pelorus:a", "SKIP", "")],
     SELF_TEST_ALLOW, 1),
    ("empty log", [], SELF_TEST_ALLOW, 2),
    ("malformed log", ["{not json"], SELF_TEST_ALLOW, 2),
    ("allow-list entry without issue",
     [record_line("pelorus:a", "OK", "")], [{"test": "pelorus:a", "reason": "SKIP: x"}], 2),
    ("allow-list reason without prefix",
     [record_line("pelorus:a", "OK", "")],
     [{"test": "pelorus:a", "reason": "no device", "issue": "#1"}], 2),
]


def self_test():
    """Plant each defect and require the expected status."""
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        log_path, allow_path = Path(tmp) / "testlog.json", Path(tmp) / "test-skips.json"
        for label, lines, allow, expected in SELF_TEST_CASES:
            log_path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
            allow_path.write_text(json.dumps(allow), encoding="utf-8")
            got = check(log_path, allow_path)
            if got != expected:
                print("self-test FAIL: %s: status %d, want %d" % (label, got, expected))
                failures += 1
    if check(Path(tmp) / "absent.json", ALLOW_LIST) != 2:
        print("self-test FAIL: missing log is not malformed")
        failures += 1
    print("self-test: %d cases, %d failures" % (len(SELF_TEST_CASES) + 1, failures))
    return 1 if failures else 0


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-2].strip())
        return 2
    return check(Path(argv[0]), ALLOW_LIST)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
