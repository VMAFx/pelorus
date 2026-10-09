#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Validate the tracked tester reports under docs/hardware-reports/ (ADR-0173, #233).

A tracked file is an intake record: `{"intake_version": 1, "credit": "", "machine": "",
"report": {<report.json of the tester program>}}`, checked against
docs/hardware-reports/intake.schema.json. The inner report must pass the tester
program's own validator (schema 2, body hash, redaction, execution class), come from a
tool whose digest is listed in tools/tester/tool-hashes.json, carry a full source commit
and a verdict of pass or fail. A failing report is welcome; incomplete and unavailable
runs are not evidence. The tool hash detects a report from a modified or unknown program
version and an accidental hand edit. It is integrity, not authenticity.

    check-reports.py [--dir DIR] [--hashes FILE]    validate every report; also checks
                                                     that the tool in this tree is listed
    check-reports.py --self-test [--disable RULE]   planted cases; each rule must go red

Exit: 0 clean (also with no reports), 1 a violation.
"""

import argparse
import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "docs" / "hardware-reports"
HASHES = ROOT / "tools" / "tester" / "tool-hashes.json"
NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-[a-z0-9][a-z0-9-]{1,60}\.json$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
ACCEPTED_VERDICTS = ("pass", "fail")
RULES = ("report_valid", "tool_hash", "name_pattern", "commit_required",
         "verdict_accepted", "credit_no_email", "envelope_leaks", "credit_anonymous",
         "tool_list_current")


def load_tester():
    path = ROOT / "tools" / "tester" / "pelorus_tester_report.py"
    spec = importlib.util.spec_from_file_location("pelorus_tester_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TESTER = load_tester()


def credit_display(envelope, disabled=frozenset()):
    """The credit to show; an empty or absent credit is recorded as anonymous."""
    credit = (envelope.get("credit") or "").strip()
    if credit or "credit_anonymous" in disabled:
        return credit
    return "anonymous"


def load_hashes(path):
    """Return ({sha256: tool_version}, [problems]) from the append-only hash list."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, ["cannot read %s: %s" % (path, exc)]
    known, errs = {}, []
    for entry in data.get("entries", []):
        sha, ver = entry.get("sha256", ""), entry.get("tool_version", "")
        if not HASH_RE.match(sha) or not VERSION_RE.match(ver):
            errs.append("%s: malformed entry %r" % (path, entry))
        elif sha in known:
            errs.append("%s: duplicate sha256 %s" % (path, sha))
        else:
            known[sha] = ver
    return known, errs


def envelope_errors(env, intake_schema, disabled):
    errs = TESTER.validate_schema(env, intake_schema)
    if errs:
        return errs
    credit = env.get("credit", "")
    if EMAIL_RE.search(credit) and "credit_no_email" not in disabled:
        errs.append("credit holds an e-mail address; give a name or a handle")
    if "envelope_leaks" not in disabled:
        meta = {"credit": credit, "machine": env.get("machine", "")}
        errs.extend("%s: unredacted %s" % leak for leak in TESTER.find_leaks(meta))
    return errs


def report_errors(rep, known, disabled):
    """Problems of the inner report. Policy rules read fields with .get so that a
    report the schema refuses still reports every policy problem it has."""
    errs = []
    if "report_valid" not in disabled:
        errs.extend(TESTER.validate_report(rep, TESTER.load_schema()))
    tool = rep.get("tool", {})
    sha, ver = tool.get("sha256", ""), tool.get("version", "")
    if "tool_hash" not in disabled and known.get(sha) != ver:
        errs.append("tool.sha256 %s.. is not a listed digest of tool version %s "
                    "(tools/tester/tool-hashes.json)" % (sha[:12], ver))
    if "commit_required" not in disabled and not COMMIT_RE.match(rep.get("commit", "")):
        errs.append("commit %r is not a full source commit" % rep.get("commit"))
    if "verdict_accepted" not in disabled and rep.get("verdict") not in ACCEPTED_VERDICTS:
        errs.append("verdict %r is not evidence; only pass and fail are accepted"
                    % rep.get("verdict"))
    return errs


def name_errors(name, rep, disabled):
    m = NAME_RE.match(name)
    if "name_pattern" in disabled:
        return []
    if not m:
        return ["file name must be YYYY-MM-DD-<slug>.json (lower case, digits, '-')"]
    if not str(rep.get("generated_utc", "")).startswith(m.group(1)):
        return ["the date in the file name is not the report's generated_utc date"]
    return []


def check_file(path, intake_schema, known, disabled=frozenset()):
    """Return the list of problems of one tracked report file."""
    try:
        env = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError as exc:
        return ["not JSON: %s" % exc]
    errs = envelope_errors(env, intake_schema, disabled)
    if errs:
        return errs
    rep = env["report"]
    return (name_errors(Path(path).name, rep, disabled)
            + report_errors(rep, known, disabled))


def report_files(directory):
    return sorted(p for p in Path(directory).glob("*.json")
                  if not p.name.endswith(".schema.json"))


def tool_list_errors(known, disabled=frozenset()):
    """The tester program in this tree must be in the hash list, or no new report passes."""
    if "tool_list_current" in disabled:
        return []
    digest, ver = TESTER.tool_digest(), TESTER.TOOL_VERSION
    if known.get(digest) == ver:
        return []
    return ["tools/tester digest %s.. (version %s) is not in tool-hashes.json; append it "
            "with: python3 -I tools/tester/pelorus_tester_report.py tool-hash" % (digest[:12], ver)]


def check_dir(directory, hashes, disabled=frozenset(), check_tool=True):
    known, problems = load_hashes(hashes)
    problems = list(problems)
    if check_tool:
        problems += tool_list_errors(known, disabled)
    schema = json.loads((Path(directory) / "intake.schema.json").read_text(encoding="utf-8"))
    failed = {}
    for path in report_files(directory):
        errs = check_file(path, schema, known, disabled)
        if errs:
            failed[path.name] = errs
    return problems, failed


def main_check(args):
    problems, failed = check_dir(args.dir, args.hashes)
    for p in problems:
        print("::error::" + p, file=sys.stderr)
    for name, errs in failed.items():
        for e in errs:
            print("::error file=%s/%s::%s" % (args.dir, name, e), file=sys.stderr)
    print("hardware reports: %d checked, %d invalid" % (len(report_files(args.dir)), len(failed)))
    return 1 if problems or failed else 0


# ----------------------------------------------------------------- self-test

BASE_TIME = "2026-01-01T00:00:00Z"


def planted_reports():
    """Yield (name, envelope): two accepted records first, then the cases to refuse."""
    rep, _ = TESTER.good_report(None, {"probe": TESTER.py_stage("import sys; sys.exit(1)")})
    rep = TESTER.reseal(rep, commit="a" * 40, generated_utc=BASE_TIME)
    env = {"intake_version": 1, "credit": "Ada", "machine": "Test rig", "report": rep}

    def variant(**changes):
        copy = json.loads(json.dumps(env))
        copy["report"] = TESTER.reseal(rep, **changes.pop("report", {}))
        copy.update(changes)
        return copy
    stages = json.loads(json.dumps(rep["stages"]))
    stages[1].update(status="incomplete", reason="timed out")
    other_tool = {**rep["tool"], "sha256": "0" * 64}
    yield "valid", env
    yield "anonymous_empty_credit", variant(credit="")
    yield "bad_schema", {**env, "report": {k: v for k, v in rep.items() if k != "host"}}
    yield "edited_body", {**env, "report": {**rep, "note": "edited by hand"}}
    yield "bad_tool_hash", variant(report={"tool": other_tool})
    yield "commit_unknown", variant(report={"commit": "unknown"})
    yield "verdict_incomplete", variant(report={
        "stages": stages, "verdict": "incomplete", "exit_code": 2,
        "failed_stages": ["libpelorus_suite"]})
    yield "email_credit", variant(credit="ada@example.org")
    yield "path_in_machine", variant(machine="rig in /home/alice/lab")
    yield "bad_name", env


# planted case -> the rule whose removal must make the case pass
EXPECT_REJECT = {
    "bad_schema": "report_valid", "edited_body": "report_valid",
    "bad_tool_hash": "tool_hash", "commit_unknown": "commit_required",
    "verdict_incomplete": "verdict_accepted", "email_credit": "credit_no_email",
    "path_in_machine": "envelope_leaks", "bad_name": "name_pattern",
}


def self_test(disabled):
    failures = []
    schema = json.loads((DIR / "intake.schema.json").read_text(encoding="utf-8"))
    known = {TESTER.tool_digest(): TESTER.TOOL_VERSION}
    cases = {}
    with tempfile.TemporaryDirectory(prefix="pelorus-intake-") as tmp:
        for name, env in planted_reports():
            path = Path(tmp) / ("Report.json" if name == "bad_name" else "2026-01-01-case.json")
            path.write_text(json.dumps(env), encoding="utf-8")
            cases[name] = check_file(path, schema, known, disabled)
    failures += ["accepts_" + n for n in ("valid", "anonymous_empty_credit") if cases[n]]
    failures += ["rejects_" + n for n in EXPECT_REJECT if not cases[n]]
    if credit_display({"credit": "  "}, disabled) != "anonymous":
        failures.append("empty_credit_is_anonymous")
    if not tool_list_errors({}, disabled):
        failures.append("rejects_unlisted_current_tool")
    for line in failures:
        print("SELF-TEST FAIL: " + line, file=sys.stderr)
    print("self-test: %s" % ("FAILED (%d)" % len(failures) if failures else "ok"))
    return 1 if failures else 0


def main(argv):
    top = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    top.add_argument("--dir", default=str(DIR))
    top.add_argument("--hashes", default=str(HASHES))
    top.add_argument("--self-test", action="store_true", dest="self_test")
    top.add_argument("--disable", action="append", choices=RULES, default=[])
    args = top.parse_args(argv)
    if args.self_test:
        return self_test(frozenset(args.disable))
    return main_check(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
