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
               have concluded `success`; none, or a newer red one, refuses. A
               run for SHA is one whose head_sha is SHA, or a run on master
               whose run name is `<workflow stem> <event> SHA` (a master
               dispatch that publishes an lts-* commit, ADR-0199);
  manual       a maintainer confirmation; recorded as `manual`, never as green.

The result (per leg: id, kind, state, evidence) is written to --output and
printed. Exit status: 0 every machine leg is green, 1 a leg is red or missing
or the legs file is malformed, 2 usage.
"""

from __future__ import annotations

import argparse
import importlib.util
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
# HISS-02: deadline for one paginated `gh api` run listing; within the 30 s
# Meson default of the candidate-legs test. A timeout fails closed, like a
# failed call.
GH_API_TIMEOUT_S = 30


def _load_json_shape():
    """Import the shared JSON shape validator from tools/tester by file path."""
    path = Path(__file__).resolve().parents[2] / "tools/tester/pelorus_json_shape.py"
    spec = importlib.util.spec_from_file_location("pelorus_json_shape", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load pelorus_json_shape from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_JSON_SHAPE = _load_json_shape()
validate_schema = _JSON_SHAPE.validate_schema

LEGS_SCHEMA = {
    "type": "object",
    "required": ["schema", "legs"],
    "properties": {
        "schema": {"const": 1},
        "legs": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "kind", "proves"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "kind": {"type": "string", "enum": sorted(KINDS)},
                    "proves": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


def validate_legs(doc: object) -> list[str]:
    """Return every schema and leg error in a parsed legs file; empty when it is valid."""
    errors = validate_schema(doc, LEGS_SCHEMA)
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return ["legs file: schema must be 1"]
    legs = doc.get("legs")
    if not isinstance(legs, list) or not legs:
        return ["legs file: legs must be a non-empty list"]
    seen: set[str] = set()
    for index, leg in enumerate(legs):
        if not isinstance(leg, dict):
            continue
        leg_id = leg.get("id")
        kind = leg.get("kind")
        if isinstance(leg_id, str) and leg_id:
            if leg_id in seen:
                errors.append(f"leg {leg_id}: duplicate id")
            else:
                seen.add(leg_id)
        if isinstance(kind, str) and kind in KINDS:
            for field in sorted(KINDS[kind]):
                if not isinstance(leg.get(field), str) or not leg[field]:
                    errors.append(f"leg {leg_id or index}: {field} missing")
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
            title = f"{Path(leg['workflow']).stem} {leg['event']} {sha}"
            matching = sorted(
                (
                    r
                    for r in runs
                    if (
                        r.get("head_sha") == sha
                        or (r.get("display_title") == title and r.get("head_branch") == "master")
                    )
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


def fetch_runs(repo: str, sha: str, doc: dict) -> list[dict]:
    """Runs of SHA itself, plus each workflow_run leg's master runs of its event."""
    queries = [f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100"]
    for leg in doc["legs"]:
        if leg["kind"] == "workflow_run":
            queries.append(
                f"repos/{repo}/actions/workflows/{leg['workflow']}/runs"
                f"?event={leg['event']}&branch=master&per_page=100"
            )
    runs: dict[object, dict] = {}
    for query in queries:
        done = subprocess.run(
            ["gh", "api", "--paginate", "--slurp", query],
            check=True,
            capture_output=True,
            text=True,
            timeout=GH_API_TIMEOUT_S,
        )
        for page in json.loads(done.stdout):
            for run in page.get("workflow_runs", []):
                runs[run.get("id")] = run
    return list(runs.values())


def _schema_failures(good: dict) -> list[str]:
    failures: list[str] = []
    schema_cases = {
        "wrong schema": {**good, "schema": 2},
        "no legs": {**good, "legs": []},
        "unknown kind": {**good, "legs": [{"id": "x", "kind": "magic", "proves": "p"}]},
        "list kind": {**good, "legs": [{"id": "x", "kind": ["magic"], "proves": "p"}]},
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

    def titled(conclusion: str, when: str, named: str, **over: str) -> dict:
        title = f"tester-publish workflow_dispatch {named}"
        fields = {"head_sha": "d" * 40, "display_title": title, "head_branch": "master", **over}
        return run(conclusion, when, **fields)

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
        ("green master dispatch naming the commit", [titled("success", "1", sha)], sha, False),
        ("green master dispatch naming another commit", [titled("success", "1", "c" * 40)], sha, True),
        ("green dispatch naming the commit off master", [titled("success", "1", sha, head_branch="lts-0.4")], sha, True),
        ("red master dispatch naming the commit", [titled("failure", "1", sha)], sha, True),
        ("named green then newer named red", [titled("success", "1", sha), titled("failure", "2", sha)], sha, True),
    ]
    for name, runs, want_sha, refused in run_cases:
        rows = evaluate(good, want_sha, runs)
        if bool(failed(rows)) != refused:
            failures.append(f"candidate legs: {name} was {'accepted' if refused else 'rejected'}")
    rows = evaluate(good, sha, [run("success", "1")])
    if [r["state"] for r in rows if r["kind"] == "manual"] != ["manual", "manual"]:
        failures.append("candidate legs: a manual leg was recorded as something else")
    failures.extend(_schema_failures(good))
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
        runs = fetch_runs(args.repo, args.sha, doc)
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
