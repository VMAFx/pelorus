#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Every candidate leg is green on the exact commit of a release candidate.

  check-candidate-legs.py --sha SHA [--repo OWNER/NAME | --runs-file FILE]
                          [--legs FILE] [--output FILE]
  check-candidate-legs.py --self-test

The legs live in scripts/release/candidate-legs.json (ADR-0176). A leg is one of

  needs        a job of release.yml that gates the release build; the build-config
               checker proves release.yml lists it, so it is recorded as gated;
  workflow_run the newest run of a workflow file for exactly SHA and event must
               have concluded `success`; none, or a newer red one, refuses;
  manual       a maintainer confirmation; recorded as `manual`, never as green.

The result (per leg: id, kind, state, evidence) is written to --output and
printed. Exit status: 0 every machine leg is green, 1 a leg is red or missing
or the legs file is malformed, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

KINDS = {
    "needs": {"job"},
    "workflow_run": {"workflow", "event"},
    "manual": set(),
}
DEFAULT_LEGS = Path(__file__).with_name("candidate-legs.json")


def validate_legs(doc: object) -> list[str]:
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return ["legs file: schema must be 1"]
    legs = doc.get("legs")
    if not isinstance(legs, list) or not legs:
        return ["legs file: legs must be a non-empty list"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, leg in enumerate(legs):
        if not isinstance(leg, dict):
            errors.append(f"leg {index}: not an object")
            continue
        leg_id = leg.get("id")
        kind = leg.get("kind")
        if not isinstance(leg_id, str) or not leg_id:
            errors.append(f"leg {index}: id missing")
        elif leg_id in seen:
            errors.append(f"leg {leg_id}: duplicate id")
        else:
            seen.add(leg_id)
        if kind not in KINDS:
            errors.append(f"leg {leg_id}: unknown kind {kind!r}")
            continue
        for field in sorted(KINDS[kind] | {"proves"}):
            if not isinstance(leg.get(field), str) or not leg[field]:
                errors.append(f"leg {leg_id}: {field} missing")
    if not any(isinstance(l, dict) and l.get("kind") == "workflow_run" for l in legs):
        errors.append("legs file: no workflow_run leg; a candidate would need no tester evidence")
    return errors


def evaluate(doc: dict, sha: str, runs: list[dict]) -> list[dict]:
    """One result row per leg, in file order."""
    rows: list[dict] = []
    for leg in doc["legs"]:
        row = {"id": leg["id"], "kind": leg["kind"], "state": "", "evidence": ""}
        if leg["kind"] == "needs":
            row["state"] = "gated"
            row["evidence"] = f"release.yml job {leg['job']}"
        elif leg["kind"] == "manual":
            row["state"] = "manual"
        else:
            matching = sorted(
                (
                    r
                    for r in runs
                    if r.get("head_sha") == sha
                    and r.get("event") == leg["event"]
                    and str(r.get("path", "")).rsplit("/", 1)[-1] == leg["workflow"]
                ),
                key=lambda r: str(r.get("created_at", "")),
            )
            if not matching:
                row["state"] = "missing"
            else:
                newest = matching[-1]
                row["state"] = "green" if newest.get("conclusion") == "success" else "red"
                row["evidence"] = str(newest.get("html_url", ""))
        rows.append(row)
    return rows


def failed(rows: list[dict]) -> list[str]:
    return [f"leg {r['id']} is {r['state']}" for r in rows if r["state"] in ("red", "missing")]


def fetch_runs(repo: str, sha: str) -> list[dict]:
    done = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [run for page in json.loads(done.stdout) for run in page.get("workflow_runs", [])]


def self_test() -> list[str]:
    failures: list[str] = []
    sha = "a" * 40
    good = json.loads(DEFAULT_LEGS.read_text(encoding="utf-8"))
    if validate_legs(good):
        return [f"candidate legs: shipped file rejected: {validate_legs(good)}"]

    def run(conclusion: str, when: str, **over: str) -> dict:
        base = {
            "path": ".github/workflows/tester-publish.yml",
            "event": "workflow_dispatch",
            "head_sha": sha,
            "conclusion": conclusion,
            "created_at": when,
            "html_url": "https://example.invalid/run",
        }
        return {**base, **over}

    run_cases = [
        ("green run", [run("success", "1")], sha, False),
        ("red run", [run("failure", "1")], sha, True),
        ("cancelled run", [run("cancelled", "1")], sha, True),
        ("no run", [], sha, True),
        ("green then newer red", [run("success", "1"), run("failure", "2")], sha, True),
        ("red then newer green", [run("failure", "1"), run("success", "2")], sha, False),
        ("green run of another commit", [run("success", "1", head_sha="b" * 40)], sha, True),
        ("green pull_request run only", [run("success", "1", event="pull_request")], sha, True),
        ("green run of another workflow", [run("success", "1", path=".github/workflows/ci.yml")], sha, True),
    ]
    for name, runs, want_sha, refused in run_cases:
        rows = evaluate(good, want_sha, runs)
        if bool(failed(rows)) != refused:
            failures.append(f"candidate legs: {name} was {'accepted' if refused else 'rejected'}")
    rows = evaluate(good, sha, [run("success", "1")])
    if [r["state"] for r in rows if r["kind"] == "manual"] != ["manual", "manual"]:
        failures.append("candidate legs: a manual leg was recorded as something else")
    schema_cases = {
        "wrong schema": {**good, "schema": 2},
        "no legs": {**good, "legs": []},
        "unknown kind": {**good, "legs": [{"id": "x", "kind": "magic", "proves": "p"}]},
        "duplicate id": {**good, "legs": good["legs"] + [good["legs"][0]]},
        "workflow_run without event": {
            **good,
            "legs": [{"id": "t", "kind": "workflow_run", "workflow": "w.yml", "proves": "p"}],
        },
        "no tester leg": {**good, "legs": [l for l in good["legs"] if l["kind"] != "workflow_run"]},
        "leg without proves": {**good, "legs": [{"id": "m", "kind": "manual"}] + good["legs"]},
    }
    for name, doc in schema_cases.items():
        if not validate_legs(doc):
            failures.append(f"candidate legs: {name} was accepted")
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--sha")
    parser.add_argument("--repo")
    parser.add_argument("--runs-file")
    parser.add_argument("--legs", default=str(DEFAULT_LEGS))
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.self_test:
        failures = self_test()
        print("\n".join(failures), file=sys.stderr)
        return 1 if failures else 0
    if not args.sha or not (args.repo or args.runs_file):
        parser.print_usage(sys.stderr)
        return 2
    doc = json.loads(Path(args.legs).read_text(encoding="utf-8"))
    errors = validate_legs(doc)
    if errors:
        print("\n".join(f"::error::{e}" for e in errors), file=sys.stderr)
        return 1
    if args.runs_file:
        runs = json.loads(Path(args.runs_file).read_text(encoding="utf-8")).get("workflow_runs", [])
    else:
        runs = fetch_runs(args.repo, args.sha)
    rows = evaluate(doc, args.sha, runs)
    result = json.dumps({"sha": args.sha, "legs": rows}, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(result, encoding="utf-8")
    print(result, end="")
    problems = failed(rows)
    for problem in problems:
        print(f"::error::candidate {args.sha[:8]}: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
