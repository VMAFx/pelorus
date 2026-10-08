#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Pelorus tester report program (ADR-0173).

Runs the tester stages in a fixed order and writes one redacted, versioned JSON
report plus SHA256SUMS and a manifest. Standard library only; run it as
`python3 -I`.

    pelorus_tester_report.py run [--plan FILE] [--out DIR] [--require-device]
                                 [--bench] [--note TEXT]
    pelorus_tester_report.py validate REPORT [--forbid LITERAL ...]
    pelorus_tester_report.py --self-test [--disable RULE]

Exit codes of `run`: 0 pass, 1 fail, 2 incomplete, 100 unavailable. A stage
that finds no hardware device is `no_device`, a reasoned non-failure, unless
`--require-device` turns it into 100 (unavailable).
"""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TOOL_NAME = "pelorus-tester-report"
TOOL_VERSION = "0.1.0"
SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("report.schema.json")

STAGE_IDS = (
    "probe",
    "libpelorus_suite",
    "registration",
    "format_matrix",
    "steering_smoke",
    "sidedata_roundtrip",
    "zero_copy_chain",
    "bench",
)
EXIT_BY_VERDICT = {"pass": 0, "fail": 1, "incomplete": 2, "unavailable": 100}
RULES = ("no_device_nonfailure", "redaction", "exit_mapping", "truncation",
         "schema_version", "reason_required", "stage_failure")

MAX_OUTPUT_BYTES = 262144
LOG_TAIL_CHARS = 4000
MAX_NODES = 200000
MAX_STRING_SCAN = 1000000
DEFAULT_TIMEOUT_S = 600
NO_DEVICE_REASON = ("no Vulkan hardware device visible; start the container "
                    "with --device /dev/dri (Intel, AMD) or --gpus all "
                    "(NVIDIA), or run on a host with a GPU driver")

# Default plan. argv None = the stage runner is not part of this kit version.
DEFAULT_PLAN = {
    "probe": {"argv": ["vulkaninfo", "--summary"], "parser": "vulkaninfo"},
    "libpelorus_suite": {"argv": None},
    "registration": {"argv": None},
    "format_matrix": {"argv": None, "needs_device": True},
    "steering_smoke": {"argv": None, "needs_device": True},
    "sidedata_roundtrip": {"argv": None},
    "zero_copy_chain": {"argv": None, "needs_device": True},
    "bench": {"argv": None, "needs_device": True, "opt_in": True},
}

UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}\b")
LABEL_RE = re.compile(r"(?i)\b((?:device|driver)UUID|deviceLUID|LUID)\b"
                      r"[ \t]*[=:]?[ \t]*\S*")
PCI_RE = re.compile(r"\b[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]\b")
USER_PATH_RE = re.compile(r"(/home/|/Users/|[A-Za-z]:\\Users\\)(?!<)[^/\\\s\"']+")
LEAK_KINDS = (("uuid", UUID_RE), ("label", LABEL_RE), ("pci", PCI_RE),
              ("user_path", USER_PATH_RE))


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":")).encode()


def report_hash(report):
    body = {k: v for k, v in report.items() if k != "report_sha256"}
    return sha256_bytes(canonical(body))


# ---------------------------------------------------------------- redaction

def local_literals():
    """Strings of this host that must never reach a report: (literal, token)."""
    found = []
    try:
        found.append((socket.gethostname(), "<host>"))
    except OSError:
        pass
    found.append((str(Path.home()), "<home>"))
    found.append((os.getcwd(), "<repo>"))
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    found.append((user, "<user>"))
    kept = [(lit, tok) for lit, tok in found if len(lit) >= 3]
    return sorted(kept, key=lambda p: -len(p[0]))


def redact_text(text, literals, disabled=frozenset()):
    if "redaction" in disabled:
        return text
    for lit, tok in literals:
        text = text.replace(lit, tok)
    text = LABEL_RE.sub(lambda m: m.group(1) + " <uuid>", text)
    text = UUID_RE.sub("<uuid>", text)
    text = PCI_RE.sub("<pci>", text)
    return USER_PATH_RE.sub(lambda m: m.group(1) + "<user>", text)


def walk_strings(obj):
    """Yield (path, string) for every key and value; iterative and bounded."""
    stack = [("$", obj)]
    for _ in range(MAX_NODES):
        if not stack:
            return
        path, node = stack.pop()
        if isinstance(node, str):
            yield path, node
        elif isinstance(node, dict):
            for key, val in node.items():
                yield path + ".<key>", str(key)
                stack.append((path + "." + str(key), val))
        elif isinstance(node, list):
            for idx, val in enumerate(node):
                stack.append(("%s[%d]" % (path, idx), val))
    raise ValueError("report exceeds %d nodes" % MAX_NODES)


def find_leaks(report, forbid=()):
    """Return (path, kind) for each unredacted identifier in the report."""
    leaks = []
    for path, text in walk_strings(report):
        for kind, rx in LEAK_KINDS:
            hit = rx.search(text)
            if hit and not (kind == "label" and hit.group(0).endswith("<uuid>")):
                leaks.append((path, kind))
        leaks.extend((path, "literal") for lit in forbid if lit and lit in text)
    return leaks


# ------------------------------------------------------------- JSON schema

def type_ok(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = {"object": dict, "array": list, "string": str, "boolean": bool,
          "null": type(None)}[name]
    return isinstance(value, py)


def check_scalar(value, sch, path, errs):
    if "type" in sch:
        names = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
        if not any(type_ok(value, n) for n in names):
            errs.append("%s: expected type %s" % (path, "|".join(names)))
            return
    if "const" in sch and value != sch["const"]:
        errs.append("%s: must equal %r" % (path, sch["const"]))
    if "enum" in sch and value not in sch["enum"]:
        errs.append("%s: %r not in enum" % (path, value))
    if isinstance(value, str):
        if "pattern" in sch and not re.search(sch["pattern"], value):
            errs.append("%s: does not match %s" % (path, sch["pattern"]))
        if len(value) > sch.get("maxLength", len(value)):
            errs.append("%s: longer than %d" % (path, sch["maxLength"]))
    if type_ok(value, "number") and value < sch.get("minimum", value):
        errs.append("%s: below minimum %s" % (path, sch["minimum"]))


def expand_node(value, sch, path, errs, stack):
    if isinstance(value, dict) and "properties" in sch:
        for req in sch.get("required", []):
            if req not in value:
                errs.append("%s: missing required '%s'" % (path, req))
        for key, val in value.items():
            if key in sch["properties"]:
                stack.append((val, sch["properties"][key], path + "." + key))
            elif sch.get("additionalProperties") is False:
                errs.append("%s: unexpected property '%s'" % (path, key))
    if isinstance(value, list) and "items" in sch:
        if len(value) < sch.get("minItems", 0):
            errs.append("%s: fewer than %d items" % (path, sch["minItems"]))
        for idx, val in enumerate(value):
            stack.append((val, sch["items"], "%s[%d]" % (path, idx)))


def validate_schema(instance, schema):
    """Check `instance` against the JSON Schema subset the schema file uses."""
    errs = []
    stack = [(instance, schema, "$")]
    for _ in range(MAX_NODES):
        if not stack:
            return errs
        value, sch, path = stack.pop()
        check_scalar(value, sch, path, errs)
        expand_node(value, sch, path, errs, stack)
    return errs + ["schema walk exceeded %d nodes" % MAX_NODES]


# ------------------------------------------------------------- verdict rules

def compute_verdict(stages, require_device, disabled=frozenset()):
    """Map stage statuses to (verdict, exit code, failed stage ids)."""
    status = [s["status"] for s in stages]
    bad = [s["id"] for s in stages if s["status"] in ("fail", "incomplete")]
    if "fail" in status and "stage_failure" not in disabled:
        return "fail", EXIT_BY_VERDICT["fail"], bad
    if "incomplete" in status:
        return "incomplete", EXIT_BY_VERDICT["incomplete"], bad
    if "no_device" in status and require_device:
        return "unavailable", EXIT_BY_VERDICT["unavailable"], bad
    if "no_device" in status and "no_device_nonfailure" in disabled:
        return "fail", EXIT_BY_VERDICT["fail"], bad
    return "pass", EXIT_BY_VERDICT["pass"], bad


def check_stages(report, disabled):
    errs = []
    stages = report.get("stages", [])
    ids = tuple(s.get("id") for s in stages if isinstance(s, dict))
    if "truncation" not in disabled:
        if ids != STAGE_IDS:
            errs.append("stages are %s, expected all of %s in order"
                        % (list(ids), list(STAGE_IDS)))
        if report.get("stage_count") != len(stages):
            errs.append("stage_count %r != %d stages"
                        % (report.get("stage_count"), len(stages)))
    if "reason_required" not in disabled:
        errs.extend("stage %s: status %s needs a reason" % (s["id"], s["status"])
                    for s in stages
                    if s.get("status") != "pass" and not s.get("reason"))
    return errs


def check_verdict(report, disabled):
    if "exit_mapping" in disabled:
        return []
    verdict, code, bad = compute_verdict(
        report["stages"], report["require_device"], disabled - {"stage_failure"})
    errs = []
    if report["verdict"] != verdict:
        errs.append("verdict %r, stages imply %r" % (report["verdict"], verdict))
    if report["exit_code"] != EXIT_BY_VERDICT.get(report["verdict"]):
        errs.append("exit_code %r does not map from verdict %r"
                    % (report["exit_code"], report["verdict"]))
    if report["failed_stages"] != bad:
        errs.append("failed_stages %r, stages imply %r"
                    % (report["failed_stages"], bad))
    return errs


def validate_report(report, schema, forbid=(), disabled=frozenset()):
    """Return a list of problems; an empty list means the report is valid."""
    sch = dict(schema)
    if "schema_version" in disabled:
        props = dict(sch["properties"])
        props["schema_version"] = {"type": "integer"}
        sch["properties"] = props
    errs = validate_schema(report, sch)
    if errs:
        return errs
    errs = check_stages(report, disabled) + check_verdict(report, disabled)
    if report["report_sha256"] != report_hash(report):
        errs.append("report_sha256 does not match the report body")
    if "redaction" not in disabled:
        errs.extend("%s: unredacted %s" % leak
                    for leak in find_leaks(report, forbid))
    return errs


# ------------------------------------------------------------ process runner

def run_command(argv, timeout_s):
    """Run argv directly, bounded in time and captured bytes.

    Returns (returncode or None, output text, error text or "")."""
    if not argv or not all(isinstance(a, str) for a in argv):
        return None, "", "argv must be a non-empty list of strings"
    with tempfile.TemporaryFile() as sink:
        try:
            proc = subprocess.Popen(argv, stdout=sink, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL,
                                    start_new_session=(os.name == "posix"))
        except FileNotFoundError:
            return None, "", "command not found: %s" % argv[0]
        except OSError as exc:
            return None, "", "cannot start %s: %s" % (argv[0], exc.strerror)
        code, err = wait_bounded(proc, timeout_s)
        sink.seek(0)
        text = sink.read(MAX_OUTPUT_BYTES).decode("utf-8", "replace")
    return code, text, err


def wait_bounded(proc, timeout_s):
    try:
        return proc.wait(timeout=timeout_s), ""
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return None, "timed out after %ss" % timeout_s


# -------------------------------------------------------------------- stages

def parse_vulkaninfo(text):
    """Extract non-identifying device facts from `vulkaninfo --summary`."""
    devices, cur = [], None
    for line in text.splitlines()[:20000]:
        if re.match(r"^GPU\d+:\s*$", line):
            cur = {"name": "", "driver": "", "api_version": "",
                   "vendor_id": "", "device_type": ""}
            devices.append(cur)
            continue
        m = re.match(r"^\s+(\w+)\s*=\s*(.*?)\s*$", line)
        if cur is None or not m:
            continue
        key = {"deviceName": "name", "driverName": "driver",
               "apiVersion": "api_version", "vendorID": "vendor_id",
               "deviceType": "device_type"}.get(m.group(1))
        if key:
            cur[key] = m.group(2)
    return devices


def is_hardware(dev):
    return "TYPE_CPU" not in dev.get("device_type", "")


def stage_record(sid, status, reason="", needs=False, **extra):
    rec = {"id": sid, "status": status, "reason": reason,
           "needs_device": needs, "duration_s": 0.0, "exit_code": None,
           "log_tail": ""}
    rec.update(extra)
    return rec


def exec_stage(sid, spec, ctx):
    """Run one stage command; returns (record, parsed devices or None)."""
    started = datetime.now().timestamp()
    code, text, err = run_command(spec["argv"], spec.get("timeout_s", DEFAULT_TIMEOUT_S))
    rec = stage_record(sid, "pass", needs=spec.get("needs_device", False),
                       exit_code=code)
    rec["log_tail"] = redact_text(text, ctx["literals"], ctx["disabled"])[-LOG_TAIL_CHARS:]
    rec["duration_s"] = round(datetime.now().timestamp() - started, 3)
    devices = parse_vulkaninfo(text) if spec.get("parser") == "vulkaninfo" else None
    if err.startswith("command not found"):
        rec.update(status="not_run", reason=err)
    elif err:
        rec.update(status="incomplete", reason=err)
    elif code != 0:
        rec.update(status="fail", reason="exit code %d" % code)
    elif spec.get("expect") and not re.search(spec["expect"], text):
        rec.update(status="fail", reason="output lacks expected /%s/" % spec["expect"])
    return rec, devices


def probe_adjust(rec, devices):
    """A failing vulkaninfo with no devices means no device, not a failure."""
    if rec["status"] in ("fail", "not_run") and not devices:
        tail = rec["log_tail"].strip().splitlines()[:1]
        why = rec["reason"] + (": " + tail[0] if tail else "")
        rec.update(status="no_device", reason=why + "; " + NO_DEVICE_REASON)
    return rec


def run_stage(sid, spec, ctx):
    needs = spec.get("needs_device", False)
    if spec.get("opt_in") and not ctx["enabled"].get(sid):
        return stage_record(sid, "not_run", "opt-in stage; pass --bench", needs)
    if needs and ctx["probed"] and not ctx["hardware"]:
        return stage_record(sid, "no_device", NO_DEVICE_REASON, needs)
    if spec.get("argv") is None:
        return stage_record(sid, "not_run",
                            "stage runner is not part of this kit version (#227)", needs)
    rec, devices = exec_stage(sid, spec, ctx)
    if devices is not None:
        ctx["probed"], ctx["devices"] = True, devices
        ctx["hardware"] = any(is_hardware(d) for d in devices)
        rec = probe_adjust(rec, devices)
    return rec


def merge_plan(plan_file):
    plan = {sid: dict(spec) for sid, spec in DEFAULT_PLAN.items()}
    if plan_file is None:
        return plan, ""
    try:
        data = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return plan, "cannot read plan %s: %s" % (plan_file, exc)
    stages = data.get("stages") if isinstance(data, dict) else None
    if not isinstance(stages, dict) or not set(stages) <= set(STAGE_IDS):
        return plan, "plan must map known stage ids under 'stages'"
    for sid, spec in stages.items():
        plan[sid].update(spec)
    return plan, ""


def build_report(plan, opts, disabled=frozenset()):
    ctx = {"literals": local_literals(), "disabled": disabled, "probed": False,
           "hardware": False, "devices": [], "enabled": {"bench": opts["bench"]}}
    stages = [run_stage(sid, plan[sid], ctx) for sid in STAGE_IDS]
    verdict, code, bad = compute_verdict(stages, opts["require_device"], disabled)
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_utc": now_utc(),
        "commit": os.environ.get("PELORUS_TESTER_COMMIT", "unknown"),
        "package": os.environ.get("PELORUS_TESTER_PACKAGE", "source-checkout"),
        "host": host_facts(),
        "devices": [{k: redact_text(v, ctx["literals"], disabled) for k, v in d.items()}
                    for d in ctx["devices"]],
        "require_device": opts["require_device"],
        "stage_count": len(stages),
        "stages": stages,
        "verdict": verdict,
        "exit_code": code,
        "failed_stages": bad,
        "note": redact_text(opts["note"], ctx["literals"], disabled),
        "redaction": {"applied": "redaction" not in disabled,
                      "rules": ["host", "home", "repo", "user", "uuid", "pci"]},
    }
    report["report_sha256"] = report_hash(report)
    return report, ctx["literals"]


def host_facts():
    import platform
    return {"os": platform.system(), "arch": platform.machine(),
            "cpu_count": os.cpu_count() or 0, "python": platform.python_version()}


# ------------------------------------------------------------------ output

def write_bundle(report, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, indent=2, sort_keys=True).encode() + b"\n"
    sums = ("%s  report.json\n" % sha256_bytes(body)).encode()
    manifest = {"files": ["report.json", "SHA256SUMS"],
                "sha256sums_sha256": sha256_bytes(sums),
                "note": "integrity only; no authenticity claim"}
    (out / "report.json").write_bytes(body)
    (out / "SHA256SUMS").write_bytes(sums)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                       encoding="utf-8")


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def cmd_run(args, disabled=frozenset()):
    plan, err = merge_plan(args.plan)
    if err:
        print("error: " + err, file=sys.stderr)
        return EXIT_BY_VERDICT["incomplete"]
    opts = {"bench": args.bench, "require_device": args.require_device,
            "note": args.note}
    report, literals = build_report(plan, opts, disabled)
    problems = validate_report(report, load_schema(), forbid=[l for l, _ in literals],
                               disabled=disabled)
    if problems:
        print("error: refusing to write an invalid report:", file=sys.stderr)
        print("\n".join("  " + p for p in problems[:20]), file=sys.stderr)
        return EXIT_BY_VERDICT["incomplete"]
    write_bundle(report, args.out)
    for st in report["stages"]:
        print("%-20s %-10s %s" % (st["id"], st["status"], st["reason"]))
    print("verdict: %s (exit %d); report: %s" % (
        report["verdict"], report["exit_code"], Path(args.out) / "report.json"))
    return report["exit_code"]


def cmd_validate(args):
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("invalid: cannot parse %s: %s" % (args.report, exc), file=sys.stderr)
        return 1
    problems = validate_report(report, load_schema(), forbid=args.forbid)
    for p in problems:
        print("invalid: " + p, file=sys.stderr)
    print("valid" if not problems else "%d problem(s)" % len(problems))
    return 1 if problems else 0


# ----------------------------------------------------------------- self-test

SYNTH_VULKANINFO = (
    "Devices:\n========\nGPU0:\n\tapiVersion         = 1.3.280\n"
    "\tvendorID           = 0x10de\n\tdeviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU\n"
    "\tdeviceName         = Planted GPU\n\tdriverName         = planted\n"
    "\tdeviceUUID         = 11111111-2222-3333-4444-555555555555\n")


def py_stage(code, **extra):
    spec = {"argv": [sys.executable, "-I", "-c", code]}
    spec.update(extra)
    return spec


def good_report(tmp, plan_stages, **opt):
    plan, _ = merge_plan(None)
    for sid, spec in plan_stages.items():
        plan[sid].update(spec)
    opts = {"bench": False, "require_device": opt.get("require_device", False),
            "note": ""}
    return build_report(plan, opts, opt.get("disabled", frozenset()))


def planted_bad(report):
    """Yield (name, mutated report) pairs the validator must reject."""
    def mutate(name, fn):
        copy = json.loads(json.dumps(report))
        fn(copy)
        copy["report_sha256"] = report_hash(copy)
        return name, copy
    yield mutate("missing_field", lambda r: r.pop("host"))
    yield mutate("uuid_present", lambda r: r.update(
        note="deviceUUID 11111111-2222-3333-4444-555555555555"))
    yield mutate("pci_bus_present", lambda r: r.update(note="at 0000:01:00.0"))
    yield mutate("user_path_present", lambda r: r.update(note="in /home/alice/x"))
    yield mutate("wrong_exit_mapping", lambda r: r.update(exit_code=0, verdict="fail"))
    yield mutate("schema_version_mismatch", lambda r: r.update(schema_version=2))
    yield mutate("truncated_stage_list", lambda r: (
        r["stages"].pop(), r.update(stage_count=len(r["stages"]))))
    yield mutate("not_run_without_reason", lambda r: r["stages"][1].update(reason=""))


def self_test(disabled=frozenset()):
    schema = load_schema()
    failures = []

    def expect(name, cond):
        if not cond:
            failures.append(name)

    cpu_only = {"probe": py_stage("import sys; sys.exit(1)")}
    rep, lit = good_report(None, cpu_only, disabled=disabled)
    expect("cpu_only_exit_0", rep["exit_code"] == 0)
    expect("cpu_only_gpu_stage_no_device",
           rep["stages"][3]["status"] == "no_device" and rep["stages"][3]["reason"])
    expect("cpu_only_valid", not validate_report(rep, schema, disabled=disabled))
    rep, _ = good_report(None, cpu_only, require_device=True, disabled=disabled)
    expect("require_device_100", rep["exit_code"] == 100)
    gpu = {"probe": py_stage("print(%r)" % SYNTH_VULKANINFO)}
    rep, lit = good_report(None, gpu, disabled=disabled)
    expect("gpu_probe_pass", rep["stages"][0]["status"] == "pass"
           and rep["devices"] and rep["stages"][3]["status"] == "not_run")
    expect("probe_log_redacted", "11111111-2222" not in json.dumps(rep) or "redaction" in disabled)
    expect("gpu_valid", not validate_report(rep, schema, disabled=disabled))
    failing = {"registration": py_stage("import sys; sys.exit(3)")}
    rep, _ = good_report(None, failing, disabled=disabled)
    expect("stage_failure_nonzero", rep["exit_code"] != 0)
    slow = {"registration": py_stage("import time; time.sleep(30)", timeout_s=1)}
    rep, _ = good_report(None, slow, disabled=disabled)
    expect("timeout_incomplete_2", rep["exit_code"] == 2)
    pat = {"registration": py_stage("print('hello')", expect="goodbye")}
    rep, _ = good_report(None, pat, disabled=disabled)
    expect("unmet_pass_rule_fails", rep["exit_code"] == 1)
    base, _ = good_report(None, cpu_only)
    for name, bad in planted_bad(base):
        expect("rejects_" + name, bool(validate_report(bad, schema, disabled=disabled)))
    expect("rejects_hostname_literal", bool(validate_report(
        base, schema, forbid=["pelorus-tester-report"], disabled=disabled)))
    expect("rejects_malformed_json", cmd_validate_text("{\"schema_ver", schema) != 0)
    for line in failures:
        print("SELF-TEST FAIL: " + line, file=sys.stderr)
    print("self-test: %s" % ("FAILED (%d)" % len(failures) if failures else "ok"))
    return 1 if failures else 0


def cmd_validate_text(text, schema):
    try:
        report = json.loads(text)
    except ValueError:
        return 1
    return 1 if validate_report(report, schema) else 0


def parse_args(argv):
    top = argparse.ArgumentParser(prog="pelorus_tester_report.py", description=__doc__.split("\n")[0])
    top.add_argument("--self-test", action="store_true", dest="self_test")
    top.add_argument("--disable", action="append", choices=RULES, default=[],
                     help="self-test mutation: switch one rule off; the self-test must fail")
    sub = top.add_subparsers(dest="cmd")
    run = sub.add_parser("run")
    run.add_argument("--plan")
    run.add_argument("--out", default="pelorus-tester-report")
    run.add_argument("--require-device", action="store_true")
    run.add_argument("--bench", action="store_true")
    run.add_argument("--note", default="")
    val = sub.add_parser("validate")
    val.add_argument("report")
    val.add_argument("--forbid", action="append", default=[])
    return top.parse_args(argv), top


def main(argv):
    args, top = parse_args(argv)
    disabled = frozenset(args.disable)
    if args.self_test:
        return self_test(disabled)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "validate":
        return cmd_validate(args)
    top.print_usage(sys.stderr)
    return EXIT_BY_VERDICT["incomplete"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
