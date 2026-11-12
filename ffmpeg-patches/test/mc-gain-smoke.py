#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Guard the mc subgroup SAD gain on devices with a measured record.

The structural SAD check protects the reduction's shape, while this smoke
checks its wall time against the former barrier tree (ADR-0256).
It subtracts a passthrough median from each mc median, then divides the
reduction-bound bsize=8 time by the fetch-bound bsize=32 time.
A device passes at or below the midpoint of its recorded subgroup and tree ratios.
A two-standard-error band or an implausibly low ratio is inconclusive.
Only devices matched by their FFmpeg name and PCI identifier are guarded.
Exit 77 names absent or inconclusive coverage; skips need a reason (ADR-0189).
The self-test uses canned data and a Python FFmpeg stub without a GPU.
Record and tree-shader modes reproduce the comparison with two builds.
Driver changes require a new record; this is not the broader GPU perf epic.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import date
from functools import partial
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import sys
import tempfile
import time
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
_recipes = importlib.import_module("doc-recipes-smoke")
ffmpeg_argv, run_ffmpeg = _recipes.ffmpeg_argv, _recipes.run_ffmpeg
resolve_ffmpeg_bin, RunResult = _recipes.resolve_ffmpeg_bin, _recipes.RunResult
_spec = importlib.util.spec_from_file_location('check-mc-sad-reduction', ROOT / 'scripts/check-mc-sad-reduction.py')  # fmt: skip
assert _spec is not None and _spec.loader is not None
_shader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shader)
strip_comments, extract_block_sad = _shader.strip_comments, _shader.extract_block_sad
validate_shader_source = _shader.validate_shader_source
PROBE_TIMEOUT_S, RUN_TIMEOUT_S = 60, 120
DEVICE_RE = re.compile('Device (\\d+) selected: (.+) \\((?:integrated|discrete|virtual|software|unknown)\\) \\((0x[0-9a-fA-F]+)\\)')  # fmt: skip
TREE = """    s_part[lidx] = d;
    barrier();
    for (uint stride = (block_dim * block_dim) / 2u; stride > 0u; stride >>= 1u) {
        if (lidx < stride)
            s_part[lidx] += s_part[lidx + stride];
        barrier();
    }
    return s_part[0];
"""


class Device(NamedTuple):
    """Identify a Vulkan device selected by FFmpeg."""

    index: int
    name: str
    device_id: str


def fields(value: object, schema: dict[str, type]) -> None:
    """Require exactly the allow-listed keys and their JSON types."""
    if type(value) is not dict or value.keys() != schema.keys():
        raise ValueError("missing or extra key")
    if any(type(value[k]) is not t for k, t in schema.items()):
        raise ValueError("wrong field type")


def validate_record(record: dict) -> dict:
    """Validate the record before running any hardware commands."""
    fields(record, dict(schema_version=int, workload=dict, devices=list, unguarded=list))  # fmt: skip
    if record["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    validate_workload(record["workload"])
    seen: set[tuple[str, str]] = set()
    for kind in ("devices", "unguarded"):
        schema = dict(name=str, device_id=str)
        extra = dict(driver=str, measured=str, runs=int, ratio_subgroup=float, ratio_tree=float)  # fmt: skip
        schema.update(extra if kind == "devices" else dict(reason=str))
        for entry in record[kind]:
            fields(entry, schema)
            if not re.fullmatch(r"0x[0-9a-fA-F]+", entry["device_id"]):
                raise ValueError("bad device_id")
            key = (entry["name"], entry["device_id"].lower())
            if key in seen:
                raise ValueError("duplicate device")
            seen.add(key)
            validate_entry(entry, kind)
    return record


def validate_workload(w: dict) -> None:
    """Require the recorded source and positive, ordered workload bounds."""
    schema = dict(source=str, size=str)
    schema.update(dict.fromkeys(('rate', 'frames', 'reduction_bsize', 'fetch_bsize'), int))  # fmt: skip
    fields(w, schema)
    if w["source"] != "testsrc2" or not re.fullmatch(r"[1-9]\d*x[1-9]\d*", w["size"]):
        raise ValueError("invalid workload source or size")
    small, big = w["reduction_bsize"], w["fetch_bsize"]
    if not (w["rate"] > 0 and w["frames"] > 0 and 8 <= small < big <= 32):
        raise ValueError("invalid workload bounds")


def validate_entry(entry: dict, kind: str) -> None:
    """Validate measured values or the issue behind an unguarded device."""
    if kind == "unguarded":
        if not re.search(r"#\d+", entry["reason"]):
            raise ValueError("unguarded reason must name #N")
        return
    sub, tree = entry["ratio_subgroup"], entry["ratio_tree"]
    if not (math.isfinite(sub) and math.isfinite(tree) and 0 < sub < tree):
        raise ValueError("ratio_tree must exceed positive ratio_subgroup")
    if entry["runs"] < 5 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["measured"]):
        raise ValueError("invalid runs or measured date")
    date.fromisoformat(entry["measured"])


def load_record(path: Path) -> dict:
    """Read a validated record and retain its path in every diagnostic."""
    try:
        return validate_record(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"record invalid: {path}: {exc}") from exc


def parse_device(output: str) -> Device | None:
    """Read the selected device name and PCI identifier from verbose output."""
    match = DEVICE_RE.search(output)
    return Device(int(match[1]), match[2], match[3].lower()) if match else None


def enumerate_devices(binary: Path, env: dict | None = None) -> list[Device]:
    """Enumerate at most eight devices and stop at the first missing index."""
    devices = []
    for index in range(8):
        args = f'-hide_banner -nostdin -v verbose -init_hw_device vulkan=vk:{index} -f lavfi -i nullsrc=size=64x64 -frames:v 1 -f null -'.split()  # fmt: skip
        result: RunResult = run_ffmpeg(
            ffmpeg_argv(binary) + args, PROBE_TIMEOUT_S, env=env
        )
        if "Unable to find device with index" in result.stdout:
            break
        device = parse_device(result.stdout)
        if device is None:
            break
        if result.returncode or device.index != index:
            raise ValueError(f"device enumeration failed: {result.stdout[-400:]}")
        devices.append(device)
    return devices


def chain(bsize: int | None) -> str:
    """Build the upload, optional mc, and download chain."""
    mc = f",pelorus_mc_vulkan=bsize={bsize}" if bsize is not None else ""
    return f"format=yuv420p,hwupload{mc},hwdownload,format=yuv420p"


def bench_time(result: RunResult, arm: str, device: Device) -> float:
    """Refuse failed arms, device changes, and missing benchmark wall times."""
    if parse_device(result.stdout) != device:
        raise ValueError(f"{arm}: device mismatch: {result.stdout[-400:]}")
    match = re.search('bench: utime=[\\d.]+s stime=[\\d.]+s rtime=([\\d.]+)s', result.stdout)  # fmt: skip
    if result.returncode or match is None:
        raise ValueError(f"{arm}: failed or missing bench line: {result.stdout[-400:]}")
    value = float(match[1])
    if not math.isfinite(value):
        raise ValueError(f"{arm}: invalid bench time: {result.stdout[-400:]}")
    return value


def measure(
    dev: Device, w: dict, arms: tuple, runs: int, env: dict | None = None
) -> dict:
    """Discard one warm-up per arm and alternate arm order in bounded rounds."""
    samples: dict[str, list[float]] = {arm[0]: [] for arm in arms}
    for round_index in range(-1, runs):
        order = arms if round_index % 2 == 0 else arms[::-1]
        for label, binary, bsize in order:
            source = f"testsrc2=size={w['size']}:rate={w['rate']}"
            args = (
                f"-hide_banner -nostdin -benchmark -v verbose -init_hw_device vulkan=vk:{dev.index} "
                f"-filter_hw_device vk -f lavfi -i {source} -frames:v {w['frames']}"
            ).split()
            args += ["-vf", chain(bsize), "-f", "null", "-"]
            value = bench_time(run_ffmpeg(ffmpeg_argv(binary) + args, RUN_TIMEOUT_S, env=env), label, dev)  # fmt: skip
            if round_index >= 0:
                samples[label].append(value)
    return samples


def stats(values: list[float]) -> tuple[float, float, float]:
    """Return the median, MAD-based sigma, and standard error of the median."""
    median = statistics.median(values)
    sigma = 1.4826 * statistics.median([abs(x - median) for x in values])
    return median, sigma, 1.2533 * sigma / math.sqrt(len(values))


def ratio(samples: dict, prefix: str = "sub") -> tuple[float, float]:
    """Subtract the passthrough median and propagate median errors into net8/net32."""
    medp, _, sep = stats(samples["passthrough"])
    med8, _, se8 = stats(samples[prefix + "8"])
    med32, _, se32 = stats(samples[prefix + "32"])
    net8, net32 = med8 - medp, med32 - medp
    if net8 <= 0 or net32 <= 0:
        raise ValueError("non-positive net time")
    r = net8 / net32
    se = r * math.sqrt((se8**2 + sep**2) / net8**2 + (se32**2 + sep**2) / net32**2)
    return r, se


def decide(r: float, se: float, entry: dict) -> tuple[int, str]:
    """Classify stale and noisy results before applying the recorded midpoint."""
    sub, tree = entry["ratio_subgroup"], entry["ratio_tree"]
    mid, gap = (sub + tree) / 2, tree - sub
    name = f"{entry['name']} {entry['device_id']}"
    if r < sub - gap:
        message = f"SKIP: inconclusive: {name}: stale record: ratio {r:.6g} below the recorded range; driver or workload changed, re-record with --make-record"
        return 77, message
    if abs(r - mid) < 2 * se:
        message = f"SKIP: inconclusive: {name}: noisy: R={r:.6g}, se_R={se:.6g}, mid={mid:.6g}"
        return 77, message
    if r <= mid:
        return 0, f"PASS: {name}: R={r:.6g} +- {se:.6g}, mid={mid:.6g}"
    message = f"FAIL: {name}: R={r:.6g} +- {se:.6g}, mid={mid:.6g}; record subgroup={sub}, tree={tree}, driver={entry['driver']}, date={entry['measured']}; ADR-0256: block_sad lost its subgroup gain, or the driver changed (rerun on an idle device; re-record after a driver change)"
    return 1, message


def judge(samples: dict, entry: dict) -> tuple[int, str]:
    """Classify a guarded device, including non-positive net measurements."""
    try:
        return decide(*ratio(samples), entry)
    except ValueError as exc:
        return 77, f"SKIP: inconclusive: {entry['name']} {entry['device_id']}: {exc}"


def summarize(results: list[tuple[int, str]]) -> tuple[int, str]:
    """Let failures dominate and print inconclusive skips last for named-skip."""
    codes = [code for code, _ in results]
    code = 1 if 1 in codes else (0 if 0 in codes and 77 not in codes else 77)
    ordered = [text for rc, text in results if rc != 77] + [text for rc, text in results if rc == 77]  # fmt: skip
    return code, "\n".join(ordered)


def check(
    binary: Path, rec: dict, runs: int, env: dict | None = None
) -> tuple[int, str]:
    """Measure only guarded devices and preserve explicit uncovered reasons."""
    results = []
    for device in enumerate_devices(binary, env):
        entry = matching_entry(rec["devices"], device)
        if entry is None:
            missing = matching_entry(rec["unguarded"], device)
            reason = missing["reason"] if missing else "no record"
            results.append((78, f'SKIP: no guarded device: {device.name} {device.device_id}: {reason}'))  # fmt: skip
            continue
        w = rec["workload"]
        arms = measurement_arms(binary, w)
        results.append(judge(measure(device, w, arms, runs, env), entry))
    return summarize(results or [(78, "SKIP: no guarded device: no Vulkan device")])


def matching_entry(entries: list[dict], device: Device) -> dict | None:
    """Match the FFmpeg name and case-normalized PCI identifier together."""
    return next((e for e in entries if (e['name'], e['device_id'].lower()) == device[1:]), None)  # fmt: skip


def measurement_arms(binary: Path, w: dict, prefix: str = "sub") -> tuple:
    """Share the same passthrough and bsize arms between check and record modes."""
    sizes = (None, w["reduction_bsize"], w["fetch_bsize"])
    return tuple(((label, binary, size) for label, size in zip(('passthrough', prefix + '8', prefix + '32'), sizes)))  # fmt: skip


def driver_map() -> dict[str, str]:
    """Read optional vulkaninfo driver metadata with a sixty-second deadline."""
    binary = shutil.which("vulkaninfo")
    if binary is None:
        return {}
    result: RunResult = run_ffmpeg([binary, "--summary"], PROBE_TIMEOUT_S)
    if result.returncode:
        return {}
    drivers = {}
    pattern = r"^\s*(deviceID|driverName|driverInfo)\s*=\s*(.*?)\s*$"
    for section in re.split(r"GPU\d+:", result.stdout)[1:]:
        values = dict(re.findall(pattern, section, re.M))
        if "deviceID" in values:
            keys = ("driverName", "driverInfo")
            driver = " ".join(values.get(k, "") for k in keys).strip()
            drivers[values["deviceID"].lower()] = driver or "unknown"
    return drivers


def make_record(binary: Path, tree: Path, workload: dict, runs: int) -> None:
    """Emit JSON device entries only when subgroup and tree ratios separate."""
    drivers = driver_map()
    for device in enumerate_devices(binary):
        arms = measurement_arms(binary, workload) + measurement_arms(tree, workload, 'tree')[1:]  # fmt: skip
        samples = measure(device, workload, arms, runs)
        sub, se_sub = ratio(samples)
        old, se_tree = ratio(samples, "tree")
        if old - sub <= 2 * math.sqrt(se_sub**2 + se_tree**2):
            message = f"no gain on {device.name} {device.device_id}: add an unguarded entry naming an issue"
            print(message, file=sys.stderr)
            continue
        entry = dict(
            name=device.name,
            device_id=device.device_id,
            runs=runs,
            driver=drivers.get(device.device_id, "unknown"),
            measured=time.strftime("%Y-%m-%d"),
            ratio_subgroup=sub,
            ratio_tree=old,
        )
        print(json.dumps(entry))


def tree_shader(source: Path, target: Path) -> None:
    """Replace the subgroup reduction and prove that the result is the tree."""
    text = source.read_bytes().decode("utf-8")
    body, _, offset = extract_block_sad(strip_comments(text))
    position = body.find("subgroupAdd(")
    if position < 0:
        raise ValueError("block_sad has no subgroupAdd(")
    start = text.rfind("\n", 0, offset + position) + 1
    output = text[:start] + TREE + text[offset + len(body) :]
    if "subgroupAdd(" in extract_block_sad(strip_comments(output))[0]:
        raise ValueError("tree block_sad still contains subgroupAdd(")
    if not validate_shader_source(output):
        raise ValueError("tree shader unexpectedly passes subgroup validation")
    target.write_bytes(output.encode("utf-8"))


def run_case(name: str, call: Callable, expected: int, fragment: str) -> bool:
    """Check one canned input against its expected status and message fragment."""
    try:
        value = call()
        rc, message = value if type(value) is tuple else (0, str(value))
    except (ValueError, OSError, AssertionError) as exc:
        rc, message = 1, str(exc)
    ok = rc == expected and fragment in message
    print(f'ok {name}' if ok else f'FAIL {name}: rc={rc}, {message!r}; expected {expected}, {fragment!r}')  # fmt: skip
    return ok


def expect(condition: bool, message: str) -> tuple[int, str]:
    """Turn a precise self-test assertion into a named case result."""
    if not condition:
        raise AssertionError(message)
    return 0, message


def canned_cases(record: dict) -> list[tuple]:
    """Cover statistics, ratio orientation, decision boundaries, and skip ordering."""
    entry = dict(record["devices"][0], ratio_subgroup=3.0, ratio_tree=5.0)
    samples = {"passthrough": [10.0] * 5, "sub8": [35.0] * 5, "sub32": [15.0] * 5}
    decisions = (
        ("pass", 3, 0, 0, "PASS"),
        ("fail", 5, 0, 1, "block_sad lost its subgroup gain"),
        ("boundary", 4, 0, 0, "PASS"),
        ("boundary-epsilon", 4 + 1e-9, 0, 1, "FAIL"),
        ("noisy", 4.1, 0.1, 77, "noisy:"),
        ("stale-low", 0.9, 0, 77, "stale record:"),
    )
    cases = [(n, partial(decide, r, se, entry), rc, msg) for n, r, se, rc, msg in decisions]  # fmt: skip
    values = (3, 1.4826, 1.2533 * 1.4826 / math.sqrt(5))
    mixed = [(77, 'SKIP: inconclusive: noisy'), (78, 'SKIP: no guarded device'), (0, 'PASS')]  # fmt: skip
    expected = (77, "SKIP: no guarded device\nPASS\nSKIP: inconclusive: noisy")
    checks = (('stats-outlier', stats([1, 2, 3, 4, 100]) == values), ('overall-inconclusive-last', summarize(mixed) == expected))  # fmt: skip
    cases += [(n, partial(expect, ok, n), 0, n) for n, ok in checks]
    nonpositive = dict(samples, sub8=[9.0] * 5)
    overall = (
        ("overall-fail-pass", [(1, "FAIL"), (0, "PASS")], 1, "FAIL"),
        ("overall-pass", [(0, "PASS")], 0, "PASS"),
        ("overall-unguarded", [(78, "no guarded device")], 77, "no guarded device"),
    )
    cases += [(n, partial(summarize, data), rc, msg) for n, data, rc, msg in overall]
    cases.append(("ratio-orientation", partial(judge, samples, entry), 1, "R=5"))
    call = partial(judge, nonpositive, entry)
    cases.append(("non-positive", call, 77, "non-positive net time"))
    return cases


def invalid_cases(record: dict, folder: Path) -> list[tuple]:
    """Exercise record rejection through the path-bearing loader diagnostic."""
    changes = (
        ("missing-key", "missing or extra key"),
        ("extra-key", "missing or extra key"),
        ("bad-ratio", "ratio_tree must exceed positive ratio_subgroup"),
        ("bad-id", "bad device_id"),
        ("no-issue", "unguarded reason must name #N"),
        ("duplicate", "duplicate device"),
    )
    cases = []
    for name, fragment in changes:
        bad = json.loads(json.dumps(record))
        if name == "missing-key":
            bad.pop("workload")
        elif name == "extra-key":
            bad["extra"] = True
        elif name == "bad-ratio":
            bad["devices"][0]["ratio_tree"] = bad["devices"][0]["ratio_subgroup"]
        elif name == "bad-id":
            bad["devices"][0]["device_id"] = "2684"
        elif name == "no-issue":
            bad["unguarded"][0]["reason"] = "slower"
        else:
            bad["devices"].append(bad["devices"][0])
        path = folder / (name + ".json")
        path.write_text(json.dumps(bad), encoding="utf-8")
        cases.append((name, partial(load_record, path), 1, f'record invalid: {path}: {fragment}'))  # fmt: skip
    return cases


STUB = """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
path = Path(os.environ["MC_GAIN_STUB"])
s = json.loads(path.read_text())
i = int(args[args.index("-init_hw_device") + 1].split(":")[-1])
probe = "-vf" not in args
if probe:
    with path.with_suffix(".log").open("a") as log:
        log.write(str(i) + "\\n")
if i >= len(s["devices"]):
    print(f"Unable to find device with index {i}!")
    print(f"Device {i} selected: decoy (unknown) (0xffff)")
    sys.exit(0)
d = s["devices"][i]
name = "wrong device" if s.get("mismatch") and not probe else d["name"]
print(f"Device {i} selected: {name} (discrete) ({d['device_id']})")
if not probe:
    t = s["times"][args[args.index("-vf") + 1]]
    print(f"bench: utime=0.1s stime=0.1s rtime={t}s")
"""


def stub_case(
    folder: Path, record: dict, names: list[dict], r: float, mismatch: bool = False
) -> tuple[int, str]:
    """Run the complete check with the Python stub and verify probe stopping."""
    stub, scenario = folder / "ffmpeg.py", folder / "scenario.json"
    stub.write_text(STUB, encoding="utf-8")
    times = {chain(None): 1, chain(8): 1 + r, chain(32): 2}
    data = dict(devices=names, times=times, mismatch=mismatch)
    scenario.write_text(json.dumps(data), encoding="utf-8")
    log = scenario.with_suffix(".log")
    log.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["MC_GAIN_STUB"] = str(scenario)
    result = check(stub, record, 5, env)
    assert log.read_text().splitlines() == [str(i) for i in range(len(names) + 1)], (
        "enumeration continued past index error"
    )
    return result


def shader_case(folder: Path) -> tuple[int, str]:
    """Prove only block_sad changes and the generated output is the barrier tree."""
    source = ROOT / "ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl"
    target = folder / "tree.glsl"
    tree_shader(source, target)
    original, output = source.read_bytes().decode(), target.read_bytes().decode()
    before, _, a = extract_block_sad(strip_comments(original))
    after, _, b = extract_block_sad(strip_comments(output))
    return expect(
        "subgroupAdd(" not in after
        and "stride >>= 1u" in after
        and bool(validate_shader_source(output))
        and original[:a] == output[:b]
        and original[a + len(before) :] == output[b + len(after) :],
        "tree and unchanged exterior",
    )


def parsing_cases(entry: dict) -> list[tuple]:
    """Cover every FFmpeg device type and benchmark parsing failures."""
    device = Device(0, entry["name"], entry["device_id"])
    line = f"Device 0 selected: {device.name} (discrete) ({device.device_id})"
    kinds = ("integrated", "discrete", "virtual", "software", "unknown")
    checks = [(f'device-{k}', parse_device(line.replace('discrete', k)) == device) for k in kinds]  # fmt: skip
    cases = [(n, partial(expect, ok, n), 0, n) for n, ok in checks]
    good = RunResult(0, line + "\nbench: utime=1s stime=2s rtime=3.5s", "")
    missing = RunResult(0, line, "")
    cases.append(("bench-ok", partial(bench_time, good, "sub8", device), 0, "3.5"))
    call = partial(bench_time, missing, "sub8", device)
    cases.append(("bench-missing", call, 1, "sub8: failed or missing bench line"))
    return cases


def self_test(record: dict) -> int:
    """Run all canned and stub cases with a fixed asserted case count."""
    with tempfile.TemporaryDirectory(prefix="mc-gain-") as directory:
        folder = Path(directory)
        entry = [record["devices"][0]]
        sub, tree = entry[0]["ratio_subgroup"], entry[0]["ratio_tree"]
        other = [dict(name="other", device_id="0xabcd")]
        rows = (
            ("device-mismatch", entry, sub, True, 1, "device mismatch"),
            ("stub-subgroup", entry, sub, False, 0, "PASS"),
            ("stub-tree", entry, tree, False, 1, "FAIL"),
            ("stub-unguarded", record["unguarded"], 3, False, 77, "(#602)"),
            ("stub-no-device", [], 3, False, 77, "no guarded device: no Vulkan device"),
            ("stub-no-record", other, 3, False, 77, "no record"),
        )
        cases = canned_cases(record) + invalid_cases(record, folder) + parsing_cases(entry[0])  # fmt: skip
        cases += [(n, partial(stub_case, folder, record, ds, r, mis), rc, msg) for n, ds, r, mis, rc, msg in rows]  # fmt: skip
        call = partial(shader_case, folder)
        cases.append(("tree-shader", call, 0, "tree and unchanged exterior"))
        source = folder / "no-subgroup.glsl"
        source.write_text("float block_sad() { return 0; }", encoding="utf-8")
        call = partial(tree_shader, source, folder / "bad.glsl")
        cases.append(("tree-refused", call, 1, "no subgroupAdd("))
        passed = [run_case(*case) for case in cases]
        assert len(passed) == 34, f"unexpected case count: {len(passed)}"
        print(f"{sum(passed)}/{len(passed)} cases passed")
        return 0 if all(passed) else 1


def main() -> int:
    """Dispatch the smoke, record generation, shader rewrite, or GPU-free tests."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--self-test", action="store_true")
    modes.add_argument("--make-record", action="store_true")
    modes.add_argument("--tree-shader", nargs=2, type=Path, metavar=("IN", "OUT"))
    parser.add_argument("--tree-ffmpeg")
    parser.add_argument('--record', type=Path, default=Path(__file__).with_name('mc-gain-record.json'))  # fmt: skip
    parser.add_argument("--runs", type=int, default=15)
    args = parser.parse_args()
    if not 5 <= args.runs <= 50:
        parser.error("--runs must be in 5..50")
    if args.make_record and not args.tree_ffmpeg:
        parser.error("--make-record requires --tree-ffmpeg")
    try:
        if args.tree_shader:
            tree_shader(*args.tree_shader)
            return 0
        record = load_record(args.record)
        if args.self_test:
            return self_test(record)
        value = os.environ.get("FFMPEG_BIN", "")
        binary = resolve_ffmpeg_bin(value) if value else None
        if binary is None:
            print("SKIP: FFMPEG_BIN unset or not found")
            return 77
        if args.make_record:
            tree = resolve_ffmpeg_bin(args.tree_ffmpeg)
            if tree is None:
                raise ValueError("tree FFmpeg not found")
            make_record(binary, tree, record["workload"], args.runs)
            return 0
        code, message = check(binary, record, args.runs)
        print(message)
        return code
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
