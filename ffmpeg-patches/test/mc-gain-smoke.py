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
from datetime import date
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import sys
import time
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
_recipes = importlib.import_module("doc-recipes-smoke")
run_ffmpeg = _recipes.run_ffmpeg
resolve_ffmpeg_bin, RunResult = _recipes.resolve_ffmpeg_bin, _recipes.RunResult
_shader = importlib.import_module("check-mc-sad-reduction")
strip_comments, extract_block_sad = _shader.strip_comments, _shader.extract_block_sad
validate_shader_source = _shader.validate_shader_source
PROBE_TIMEOUT_S, RUN_TIMEOUT_S = 60, 120
RUN_BUDGET_S = 540
MAX_DEVICES = 8
DEVICE_RE = re.compile(
    r"Device (\d+) selected: (.+) "
    r"\((?:integrated|discrete|virtual|software|unknown)\) \((0x[0-9a-fA-F]+)\)"
)
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


def ffmpeg_argv(binary: Path) -> list[str]:
    """Reuse the recipe command prefix and isolate Python stub interpreters."""
    args = _recipes.ffmpeg_argv(binary)
    return [args[0], "-I", *args[1:]] if binary.suffix == ".py" else args


def fields(value: object, schema: dict[str, type]) -> None:
    """Require exactly the allow-listed keys and their JSON types."""
    if not isinstance(value, dict) or value.keys() != schema.keys():
        raise ValueError("missing or extra key")
    if any(
        not isinstance(value[k], t) or (isinstance(value[k], bool) and t is not bool)
        for k, t in schema.items()
    ):
        raise ValueError("wrong field type")


def entry_schema(kind: str) -> dict[str, type]:
    """Share the ordered device field schema with record emission."""
    schema = dict(name=str, device_id=str)
    extra = dict(
        driver=str,
        measured=str,
        runs=int,
        workload_sha256=str,
        ratio_subgroup=float,
        ratio_tree=float,
    )
    schema.update(extra if kind == "devices" else dict(reason=str))
    return schema


def validate_record(record: dict, *, require_workload_match: bool) -> dict:
    """Validate the record before running any hardware commands."""
    fields(
        record, dict(schema_version=int, workload=dict, devices=list, unguarded=list)
    )
    if record["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    validate_workload(record["workload"])
    seen: set[tuple[str, str]] = set()
    for kind in ("devices", "unguarded"):
        schema = entry_schema(kind)
        for entry in record[kind]:
            fields(entry, schema)
            if not re.fullmatch(r"0x[0-9a-fA-F]+", entry["device_id"]):
                raise ValueError("bad device_id")
            key = (entry["name"], entry["device_id"].lower())
            if key in seen:
                raise ValueError("duplicate device")
            seen.add(key)
            validate_entry(entry, kind)
            if (
                require_workload_match
                and kind == "devices"
                and entry["workload_sha256"] != workload_fingerprint(record["workload"])
            ):
                raise ValueError(
                    f"device {entry['name']} {entry['device_id']} measured with another workload; "
                    "re-record with --make-record"
                )
    return record


def validate_workload(w: dict) -> None:
    """Require a positive source workload with the exact net8/net32 block sizes."""
    schema = dict(source=str, size=str)
    schema.update(
        dict.fromkeys(("rate", "frames", "reduction_bsize", "fetch_bsize"), int)
    )
    fields(w, schema)
    if w["source"] != "testsrc2" or not re.fullmatch(r"[1-9]\d*x[1-9]\d*", w["size"]):
        raise ValueError("invalid workload source or size")
    small, big = w["reduction_bsize"], w["fetch_bsize"]
    if not (w["rate"] > 0 and w["frames"] > 0 and small == 8 and big == 32):
        raise ValueError("invalid workload bounds")


def workload_fingerprint(w: dict) -> str:
    """Bind device measurements to the canonical JSON workload with SHA-256."""
    encoded = json.dumps(w, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


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


def load_record(path: Path, *, require_workload_match: bool = True) -> dict:
    """Read a validated record and retain its path in every diagnostic."""
    try:
        return validate_record(
            json.loads(path.read_text(encoding="utf-8")),
            require_workload_match=require_workload_match,
        )
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"record invalid: {path}: {exc}") from exc


def parse_device(output: str) -> Device | None:
    """Read the selected device name and PCI identifier from verbose output."""
    match = DEVICE_RE.search(output)
    return Device(int(match[1]), match[2], match[3].lower()) if match else None


def budget_error(deadline: float, context: str) -> ValueError:
    """Name the total budget, elapsed time, and interrupted stage."""
    elapsed = time.monotonic() - deadline + RUN_BUDGET_S
    return ValueError(
        f"mc-gain budget {RUN_BUDGET_S} s exhausted after {elapsed:.3f} s at {context}"
    )


def run_stage(
    argv: list[str],
    stage_timeout: float,
    context: str,
    env: dict | None = None,
    deadline: float | None = None,
) -> RunResult:
    """Clamp each stage to the remaining check budget and diagnose exhaustion."""
    remaining = stage_timeout if deadline is None else deadline - time.monotonic()
    if deadline is not None and remaining <= 0:
        raise budget_error(deadline, context)
    timeout = min(stage_timeout, remaining)
    result = run_ffmpeg(argv, timeout, env=env)
    if deadline is not None and timeout < stage_timeout and result.returncode == 124:
        raise budget_error(deadline, context)
    return result


def enumerate_devices(
    binary: Path, env: dict | None = None, deadline: float | None = None
) -> list[Device]:
    """Require explicit end markers and refuse incomplete or capped enumeration."""
    devices = []
    for index in range(MAX_DEVICES):
        args = (
            f"-hide_banner -nostdin -v verbose -init_hw_device vulkan=vk:{index} "
            "-f lavfi -i nullsrc=size=64x64 -frames:v 1 -f null -"
        ).split()
        result = run_stage(
            ffmpeg_argv(binary) + args,
            PROBE_TIMEOUT_S,
            f"device {index} enumeration",
            env,
            deadline,
        )
        if f"Unable to find device with index {index}!" in result.stdout:
            return devices
        if index == 0 and "No devices found" in result.stdout:
            return []
        device = parse_device(result.stdout)
        if device is None or result.returncode or device.index != index:
            raise ValueError(f"device enumeration failed: {result.stdout[-400:]}")
        devices.append(device)
    raise ValueError(
        f"device enumeration reached cap {MAX_DEVICES} without an end marker"
    )


def chain(bsize: int | None) -> str:
    """Build the upload, optional mc, and download chain."""
    mc = f",pelorus_mc_vulkan=bsize={bsize}" if bsize is not None else ""
    return f"format=yuv420p,hwupload{mc},hwdownload,format=yuv420p"


def bench_time(result: RunResult, arm: str, device: Device) -> float:
    """Refuse failed arms, device changes, and missing benchmark wall times."""
    if parse_device(result.stdout) != device:
        raise ValueError(f"{arm}: device mismatch: {result.stdout[-400:]}")
    match = re.search(
        "bench: utime=[\\d.]+s stime=[\\d.]+s rtime=([\\d.]+)s", result.stdout
    )
    if result.returncode or match is None:
        raise ValueError(f"{arm}: failed or missing bench line: {result.stdout[-400:]}")
    value = float(match[1])
    if not math.isfinite(value):
        raise ValueError(f"{arm}: invalid bench time: {result.stdout[-400:]}")
    return value


def measure(
    dev: Device,
    w: dict,
    arms: tuple,
    runs: int,
    env: dict | None = None,
    deadline: float | None = None,
) -> dict:
    """Discard one warm-up per arm and alternate arm order in bounded rounds."""
    samples: dict[str, list[float]] = {arm[0]: [] for arm in arms}
    for round_index in range(-1, runs):
        order = arms if round_index % 2 == 0 else arms[::-1]
        for label, binary, bsize in order:
            source = f"testsrc2=size={w['size']}:rate={w['rate']}"
            args = (
                "-hide_banner -nostdin -benchmark -v verbose "
                f"-init_hw_device vulkan=vk:{dev.index} "
                f"-filter_hw_device vk -f lavfi -i {source} -frames:v {w['frames']}"
            ).split()
            args += ["-vf", chain(bsize), "-f", "null", "-"]
            value = bench_time(
                run_stage(
                    ffmpeg_argv(binary) + args,
                    RUN_TIMEOUT_S,
                    f"device {dev.index} {dev.name} {dev.device_id} arm {label}",
                    env,
                    deadline,
                ),
                label,
                dev,
            )
            if round_index >= 0:
                samples[label].append(value)
    return samples


def stats(values: list[float]) -> tuple[float, float, float]:
    """Return the median, MAD-based sigma, and standard error of the median."""
    median = statistics.median(values)
    sigma = 1.4826 * statistics.median([abs(x - median) for x in values])
    return median, sigma, 1.2533 * sigma / math.sqrt(len(values))


def ratio_terms(samples: dict, prefix: str) -> tuple[float, float, float, float, float]:
    """Share the medians, positive net times, and errors used by both deltas."""
    medp, _, sep = stats(samples["passthrough"])
    med8, _, se8 = stats(samples[prefix + "8"])
    med32, _, se32 = stats(samples[prefix + "32"])
    net8, net32 = med8 - medp, med32 - medp
    if net8 <= 0 or net32 <= 0:
        raise ValueError("non-positive net time")
    return net8 / net32, net32, se8, se32, sep


def ratio(samples: dict, prefix: str = "sub") -> tuple[float, float]:
    """Propagate se_R with shared passthrough derivative (R-1)/net32."""
    r, net32, se8, se32, sep = ratio_terms(samples, prefix)
    se = math.sqrt(se8**2 + r**2 * se32**2 + (r - 1) ** 2 * sep**2) / net32
    return r, se


def decide(r: float, se: float, entry: dict) -> tuple[int, str]:
    """Classify stale and noisy results before applying the recorded midpoint."""
    sub, tree = entry["ratio_subgroup"], entry["ratio_tree"]
    mid, gap = (sub + tree) / 2, tree - sub
    name = f"{entry['name']} {entry['device_id']}"
    if r < sub - gap:
        message = (
            f"SKIP: inconclusive: {name}: stale record: ratio {r:.6g} "
            "below the recorded range; driver or workload changed, "
            "re-record with --make-record"
        )
        return 77, message
    if abs(r - mid) < 2 * se:
        message = f"SKIP: inconclusive: {name}: noisy: R={r:.6g}, se_R={se:.6g}, mid={mid:.6g}"
        return 77, message
    if r <= mid:
        return 0, f"PASS: {name}: R={r:.6g} +- {se:.6g}, mid={mid:.6g}"
    message = (
        f"FAIL: {name}: R={r:.6g} +- {se:.6g}, mid={mid:.6g}; "
        f"record subgroup={sub}, tree={tree}, driver={entry['driver']}, "
        f"date={entry['measured']}; ADR-0256: block_sad lost its subgroup gain, "
        "or the driver changed (rerun on an idle device; "
        "re-record after a driver change)"
    )
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
    ordered = [text for rc, text in results if rc != 77] + [
        text for rc, text in results if rc == 77
    ]
    return code, "\n".join(ordered)


def check(
    binary: Path,
    rec: dict,
    runs: int,
    env: dict | None = None,
    drivers: dict[tuple[str, str], str] | None = None,
    deadline: float | None = None,
) -> tuple[int, str]:
    """Measure guarded devices only after checking known driver metadata."""
    if deadline is None:
        deadline = time.monotonic() + RUN_BUDGET_S
    if drivers is None:
        drivers = driver_map(deadline)
    results = []
    for device in enumerate_devices(binary, env, deadline):
        entry = matching_entry(rec["devices"], device)
        if entry is None:
            missing = matching_entry(rec["unguarded"], device)
            reason = missing["reason"] if missing else "no record"
            results.append(
                (
                    78,
                    f"SKIP: no guarded device: {device.name} {device.device_id}: {reason}",
                )
            )
            continue
        current = drivers.get(device[1:], "unknown")
        if current != "unknown" and current != entry["driver"]:
            results.append(
                (
                    77,
                    f"SKIP: inconclusive: {device.name} {device.device_id}: stale record: "
                    f"driver {current} differs from recorded {entry['driver']}; "
                    "re-record with --make-record",
                )
            )
            continue
        w = rec["workload"]
        arms = measurement_arms(binary, w)
        results.append(judge(measure(device, w, arms, runs, env, deadline), entry))
    return summarize(results or [(78, "SKIP: no guarded device: no Vulkan device")])


def matching_entry(entries: list[dict], device: Device) -> dict | None:
    """Match the FFmpeg name and case-normalized PCI identifier together."""
    return next(
        (e for e in entries if (e["name"], e["device_id"].lower()) == device[1:]), None
    )


def measurement_arms(binary: Path, w: dict, prefix: str = "sub") -> tuple:
    """Share the same passthrough and bsize arms between check and record modes."""
    sizes = (None, w["reduction_bsize"], w["fetch_bsize"])
    return tuple(
        (
            (label, binary, size)
            for label, size in zip(("passthrough", prefix + "8", prefix + "32"), sizes)
        )
    )


def driver_map(deadline: float | None = None) -> dict[tuple[str, str], str]:
    """Read optional vulkaninfo driver metadata with a sixty-second deadline."""
    binary = shutil.which("vulkaninfo")
    if binary is None:
        return {}
    result = run_stage(
        [binary, "--summary"],
        PROBE_TIMEOUT_S,
        "driver metadata vulkaninfo",
        deadline=deadline,
    )
    if result.returncode:
        return {}
    return parse_drivers(result.stdout)


def parse_drivers(text: str) -> dict[tuple[str, str], str]:
    """Parse summary sections by device name and PCI ID without merging ICDs."""
    drivers = {}
    pattern = r"^\s*(deviceName|deviceID|driverName|driverInfo)\s*=\s*(.*?)\s*$"
    for section in re.split(r"^\s*GPU\d+:", text, flags=re.M)[1:]:
        values = dict(re.findall(pattern, section, re.M))
        if "deviceName" in values and "deviceID" in values:
            keys = ("driverName", "driverInfo")
            driver = " ".join(values.get(k, "") for k in keys).strip()
            drivers[(values["deviceName"], values["deviceID"].lower())] = (
                driver or "unknown"
            )
    return drivers


def separation(samples: dict) -> tuple[float, float]:
    """Propagate the ratio gap with its shared passthrough covariance."""
    rs, ns, e8s, e32s, sep = ratio_terms(samples, "sub")
    rt, nt, e8t, e32t, _ = ratio_terms(samples, "tree")
    variance = (
        (e8s / ns) ** 2
        + (rs * e32s / ns) ** 2
        + (e8t / nt) ** 2
        + (rt * e32t / nt) ** 2
        + ((rt - 1) / nt - (rs - 1) / ns) ** 2 * sep**2
    )
    return rt - rs, math.sqrt(variance)


def separated(samples: dict) -> bool:
    """Require the gain to exceed twice the standard error of the gap."""
    gap, se_gap = separation(samples)
    return gap > 2 * se_gap


def make_record(binary: Path, tree: Path, workload: dict, runs: int) -> int:
    """Count emitted entries whose separated ratios validate after rounding."""
    drivers = driver_map()
    emitted = 0
    for device in enumerate_devices(binary):
        arms = (
            measurement_arms(binary, workload)
            + measurement_arms(tree, workload, "tree")[1:]
        )
        samples = measure(device, workload, arms, runs)
        sub, _ = ratio(samples)
        old, _ = ratio(samples, "tree")
        entry = dict(
            name=device.name,
            device_id=device.device_id,
            driver=drivers.get(device[1:], "unknown"),
            measured=time.strftime("%Y-%m-%d"),
            runs=runs,
            workload_sha256=workload_fingerprint(workload),
        )
        metadata = json.dumps(entry)[:-1]
        line = f'{metadata}, "ratio_subgroup": {sub:.3f}, "ratio_tree": {old:.3f}}}'
        valid = True
        try:
            printed = json.loads(line)
            fields(printed, entry_schema("devices"))
            validate_entry(printed, "devices")
        except ValueError:
            valid = False
        if not separated(samples) or not valid:
            message = (
                f"no gain on {device.name} {device.device_id}: "
                "add an unguarded entry naming an issue"
            )
            print(message, file=sys.stderr)
            continue
        print(line)
        emitted += 1
    return emitted


def reduction_start(text: str, position: int) -> int:
    """Include the contiguous comment block immediately before the reduction."""
    start = text.rfind("\n", 0, position) + 1
    pattern = r"(?:^[ \t]*//[^\n]*\n)+\Z|^[ \t]*/\*(?:(?!/\*).)*?\*/[ \t]*\n\Z"
    comment = re.search(pattern, text[:start], re.M | re.S)
    return comment.start() if comment else start


def tree_shader(source: Path, target: Path) -> None:
    """Refuse aliased output and replace only the subgroup reduction with a tree."""
    if source.resolve() == target.resolve() or (
        target.exists() and os.path.samefile(source, target)
    ):
        raise ValueError("tree shader output aliases its input")
    text = source.read_bytes().decode("utf-8")
    body, _, offset = extract_block_sad(strip_comments(text))
    position = body.find("subgroupAdd(")
    if position < 0:
        raise ValueError("block_sad has no subgroupAdd(")
    start = reduction_start(text, offset + position)
    output = text[:start] + TREE + text[offset + len(body) :]
    if "subgroupAdd(" in extract_block_sad(strip_comments(output))[0]:
        raise ValueError("tree block_sad still contains subgroupAdd(")
    if not validate_shader_source(output):
        raise ValueError("tree shader unexpectedly passes subgroup validation")
    target.write_bytes(output.encode("utf-8"))


def record_mode(binary: Path, tree_value: str, workload: dict, runs: int) -> int:
    """Resolve the comparison binary and fail when no record entry is emitted."""
    tree = resolve_ffmpeg_bin(tree_value)
    if tree is None:
        raise ValueError("tree FFmpeg not found")
    if not make_record(binary, tree, workload, runs):
        print("no device entries emitted", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Dispatch the smoke, record generation, shader rewrite, or GPU-free tests."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--self-test", action="store_true")
    modes.add_argument("--make-record", action="store_true")
    modes.add_argument("--tree-shader", nargs=2, type=Path, metavar=("IN", "OUT"))
    parser.add_argument("--tree-ffmpeg")
    parser.add_argument(
        "--record", type=Path, default=Path(__file__).with_name("mc-gain-record.json")
    )
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
        record = load_record(args.record, require_workload_match=not args.make_record)
        if args.self_test:
            selftest = importlib.import_module("mc-gain-self-test")
            return selftest.self_test(sys.modules[__name__])
        value = os.environ.get("FFMPEG_BIN", "")
        binary = resolve_ffmpeg_bin(value) if value else None
        if binary is None:
            print("SKIP: FFMPEG_BIN unset or not found")
            return 77
        if args.make_record:
            return record_mode(binary, args.tree_ffmpeg, record["workload"], args.runs)
        code, message = check(binary, record, args.runs)
        print(message)
        return code
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
