# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Test the mc gain runner with canned samples and a GPU-free FFmpeg stub.

The runner passes itself to self_test() only when --self-test is selected.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import redirect_stdout, redirect_stderr
import io
from functools import partial
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from unittest.mock import patch

STUB_FFMPEG = Path(__file__).resolve().with_name("mc-gain-stub-ffmpeg.py")


def run_case(name: str, call: Callable, expected: int, fragment: str) -> bool:
    """Check one canned input against its expected status and message fragment."""
    try:
        value = call()
        rc, message = value if isinstance(value, tuple) else (0, str(value))
    except (ValueError, OSError, AssertionError, ArithmeticError) as exc:
        rc, message = 1, str(exc)
    ok = rc == expected and fragment in message
    print(
        f"ok {name}"
        if ok
        else f"FAIL {name}: rc={rc}, {message!r}; expected {expected}, {fragment!r}"
    )
    return ok


def expect(condition: bool, message: str) -> tuple[int, str]:
    """Turn a precise self-test assertion into a named case result."""
    if not condition:
        raise AssertionError(message)
    return 0, message


def canned_cases(runner: ModuleType, record: dict) -> list[tuple]:
    """Cover statistics, ratio orientation, decision boundaries, and skip ordering."""
    entry = dict(record["devices"][0], ratio_subgroup=3.0, ratio_tree=5.0)
    samples = {"passthrough": [10.0] * 5, "sub8": [35.0] * 5, "sub32": [15.0] * 5}
    decisions = (
        ("pass", 3, 0, 0, "PASS"),
        ("fail", 5, 0, 1, "block_sad lost its subgroup gain"),
        ("boundary", 4, 0, 0, "PASS"),
        ("boundary-epsilon", 4 + 1e-9, 0, 1, "FAIL"),
        ("noisy", 4.1, 0.1, 77, "noisy:"),
        ("noisy-1.5-se", 4.15, 0.1, 77, "noisy:"),
        ("stale-range-pass", 1.5, 0, 0, "PASS"),
        ("stale-just-below", 1 - 1e-9, 0, 77, "stale record:"),
        ("stale-low", 0.9, 0, 77, "stale record:"),
        ("stale-before-noisy", 0.5, 3.0, 77, "stale record:"),
    )
    cases = [
        (n, partial(runner.decide, r, se, entry), rc, msg)
        for n, r, se, rc, msg in decisions
    ]
    values = (3, 1.4826, 1.2533 * 1.4826 / math.sqrt(5))
    mixed = [(77, "inconclusive"), (78, "unguarded"), (0, "PASS")]
    expected = (77, "unguarded\nPASS\ninconclusive")
    checks = (
        ("stats-outlier", runner.stats([1, 2, 3, 4, 100]) == values),
        ("overall-inconclusive-last", runner.summarize(mixed) == expected),
        ("separation-2-se", not runner.separated(3.0, 0.0, 4.0, 0.5)),
    )
    cases += [(n, partial(expect, ok, n), 0, n) for n, ok in checks]
    overall = (
        ("overall-fail-pass", [(1, "FAIL"), (0, "PASS")], 1, "FAIL"),
        ("overall-pass", [(0, "PASS")], 0, "PASS"),
        ("overall-unguarded", [(78, "no guarded device")], 77, "no guarded device"),
    )
    cases += [
        (n, partial(runner.summarize, data), rc, msg) for n, data, rc, msg in overall
    ]
    cases.append(("ratio-orientation", partial(runner.judge, samples, entry), 1, "R=5"))
    call = partial(runner.judge, dict(samples, sub8=[9.0] * 5), entry)
    cases.append(("non-positive", call, 77, "non-positive net time"))
    for arm in ("sub8", "sub32"):
        call = partial(runner.judge, dict(samples, **{arm: [10.0] * 5}), entry)
        cases.append((f"zero-net-{arm}", call, 77, "non-positive net time"))
    return cases


def invalid_mutations() -> tuple[tuple[str, Callable, str], ...]:
    """Describe independent invalid records and their required diagnostics."""
    bounds, keys = "invalid workload bounds", "missing or extra key"
    stale = "measured with another workload; re-record with --make-record"
    d = lambda b: b["devices"][0]
    w = lambda b: b["workload"]
    equal_ratio = lambda b: d(b).update(ratio_tree=d(b)["ratio_subgroup"])
    duplicate_ids = lambda b: b["devices"].extend(
        dict(d(b), device_id=i) for i in ("0x13c0", "0x13C0")
    )
    ratio_error = "ratio_tree must exceed positive ratio_subgroup"
    issue_error = "unguarded reason must name #N"
    date_error = "invalid runs or measured date"
    return (
        ("missing-key", lambda b: b.pop("workload"), keys),
        ("extra-key", lambda b: b.update(extra=True), keys),
        ("bad-ratio", equal_ratio, ratio_error),
        ("bad-id", lambda b: d(b).update(device_id="2684"), "bad device_id"),
        ("runs-four", lambda b: d(b).update(runs=4), "invalid runs"),
        ("bsize-seven", lambda b: w(b).update(reduction_bsize=7), bounds),
        ("bsize-16", lambda b: w(b).update(reduction_bsize=16), bounds),
        ("fetch-bsize-16", lambda b: w(b).update(fetch_bsize=16), bounds),
        ("bsize-33", lambda b: w(b).update(fetch_bsize=33), bounds),
        ("rate-zero", lambda b: w(b).update(rate=0), bounds),
        ("frames-zero", lambda b: w(b).update(frames=0), bounds),
        ("wrong-workload", lambda b: d(b).update(workload_sha256="0" * 16), stale),
        ("changed-workload", lambda b: w(b).update(frames=65), stale),
        ("missing-workload-hash", lambda b: d(b).pop("workload_sha256", None), keys),
        ("bool-int", lambda b: d(b).update(runs=True), "wrong field type"),
        ("int-float", lambda b: d(b).update(ratio_subgroup=1), "wrong field type"),
        ("no-issue", lambda b: b["unguarded"][0].update(reason="slower"), issue_error),
        ("duplicate", lambda b: b["devices"].append(d(b)), "duplicate device"),
        ("duplicate-id-case", duplicate_ids, "duplicate device"),
        ("equal-workload-bsize", lambda b: w(b).update(reduction_bsize=32), bounds),
        ("date-nonpadded", lambda b: d(b).update(measured="2026-1-12"), ""),
        ("date-basic", lambda b: d(b).update(measured="20260112"), date_error),
    )


def invalid_cases(runner: ModuleType, record: dict, folder: Path) -> list[tuple]:
    """Exercise invalid records and the accepted lower run-count boundary."""
    cases = []
    for name, mutate, fragment in invalid_mutations():
        bad = json.loads(json.dumps(record))
        mutate(bad)
        path = folder / (name + ".json")
        path.write_text(json.dumps(bad), encoding="utf-8")
        if fragment.startswith("measured with another workload"):
            device = bad["devices"][0]
            fragment = f"device {device['name']} {device['device_id']} {fragment}"
        call = partial(runner.load_record, path)
        cases.append((name, call, 1, f"record invalid: {path}: {fragment}"))
    good = json.loads(json.dumps(record))
    good["devices"][0]["runs"] = 5
    path = folder / "runs-five.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    cases.append(("runs-five", partial(runner.load_record, path), 0, ""))
    return cases


def cli_cases(runner: ModuleType) -> list[tuple]:
    """Check CLI argument refusals before the missing-binary skip."""
    path = Path(runner.__file__)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.pop("FFMPEG_BIN", None)
    invalid = "--runs must be in 5..50"
    skipped = "SKIP: FFMPEG_BIN unset or not found"
    cases = []
    for name, argv, expected, fragment in (
        ("cli-runs-4", ["--runs", "4"], 2, invalid),
        ("cli-runs-51", ["--runs", "51"], 2, invalid),
        ("cli-runs-5", ["--runs", "5"], 77, skipped),
        ("cli-runs-50", ["--runs", "50"], 77, skipped),
        ("cli-record-no-tree", ["--make-record"], 2, "requires --tree-ffmpeg"),
    ):
        result = runner.run_ffmpeg(
            [sys.executable, "-I", str(path), *argv],
            10,
            env=env,
            merge_stderr=False,
        )
        message = result.stderr if expected == 2 else result.stdout
        detail = f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        ok = result.returncode == expected and fragment in message
        call = partial(expect, ok, detail)
        cases.append((name, call, 0, fragment))
    return cases


def stub_fixture(
    runner: ModuleType,
    folder: Path,
    names: list[dict],
    r: float,
    mismatch: bool = False,
    probe: dict[str, object] | None = None,
) -> tuple[Path, dict[str, str]]:
    """Write a stub scenario and retain the inherited Git isolation barrier."""
    scenario = folder / "scenario.json"
    times = {runner.chain(None): 1, runner.chain(8): 1 + r, runner.chain(32): 2}
    data = dict(devices=names, times=times, mismatch=mismatch)
    data.update(probe or {})
    scenario.write_text(json.dumps(data), encoding="utf-8")
    log = scenario.with_suffix(".log")
    log.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["MC_GAIN_STUB"] = str(scenario)
    scenario.with_suffix(".calls").write_text("", encoding="utf-8")
    return scenario, env


def stub_case(
    runner: ModuleType,
    folder: Path,
    record: dict,
    names: list[dict],
    r: float,
    mismatch: bool = False,
) -> tuple[int, str]:
    """Run the complete check with the Python stub and verify probe stopping."""
    scenario, env = stub_fixture(runner, folder, names, r, mismatch)
    log = scenario.with_suffix(".log")
    with patch.object(runner, "driver_map", return_value={}):
        result = runner.check(STUB_FFMPEG, record, 5, env)
    assert log.read_text().splitlines() == [str(i) for i in range(len(names) + 1)], (
        "enumeration continued past index error"
    )
    return result


def id_case(
    runner: ModuleType, folder: Path, record: dict, uppercase_record: bool
) -> tuple[int, str]:
    """Match opposite-case record and printed PCI identifiers through measurement."""
    lower, upper = "0x13c0", "0x13C0"
    entry = dict(record["devices"][0], device_id=upper if uppercase_record else lower)
    printed = [dict(entry, device_id=lower if uppercase_record else upper)]
    ratio = float(entry["ratio_subgroup"])
    return stub_case(runner, folder, dict(record, devices=[entry]), printed, ratio)


def shader_case(runner: ModuleType, folder: Path) -> tuple[int, str]:
    """Prove only block_sad changes and the generated output is the barrier tree."""
    source = runner.ROOT / "ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl"
    target = folder / "tree.glsl"
    runner.tree_shader(source, target)
    original, output = source.read_bytes().decode(), target.read_bytes().decode()
    before, _, offset = runner.extract_block_sad(runner.strip_comments(original))
    after = runner.extract_block_sad(runner.strip_comments(output))[0]
    start = original.index("    /* Two-level reduction:")
    end = offset + len(before)
    replacement_end = start + len(runner.TREE)
    return expect(
        "subgroupAdd" not in output[start:replacement_end]
        and "Two-level reduction" not in output
        and "stride >>= 1u" in after
        and bool(runner.validate_shader_source(output))
        and original[:start] == output[:start]
        and original[end:] == output[replacement_end:],
        "tree and unchanged exterior",
    )


def parsing_cases(runner: ModuleType, entry: dict) -> list[tuple]:
    """Cover every FFmpeg device type and benchmark parsing failures."""
    device = runner.Device(0, entry["name"], entry["device_id"])
    line = f"Device 0 selected: {device.name} (discrete) ({device.device_id})"
    kinds = ("integrated", "discrete", "virtual", "software", "unknown")
    checks = [
        (f"device-{k}", runner.parse_device(line.replace("discrete", k)) == device)
        for k in kinds
    ]
    cases = [(n, partial(expect, ok, n), 0, n) for n, ok in checks]
    good = runner.RunResult(0, line + "\nbench: utime=1s stime=2s rtime=3.5s", "")
    missing = runner.RunResult(0, line, "")
    cases.append(
        ("bench-ok", partial(runner.bench_time, good, "sub8", device), 0, "3.5")
    )
    call = partial(runner.bench_time, missing, "sub8", device)
    cases.append(("bench-missing", call, 1, "sub8: failed or missing bench line"))
    return cases


def ratio_case(runner: ModuleType, unity: bool = False) -> tuple[int, str]:
    """Pin R and its propagated error with spread in every arm."""
    samples = {
        "passthrough": [1, 2, 3, 4, 5],
        "sub8": [18, 18, 21, 24, 24],
        "sub32": [7, 8, 9, 10, 11],
    }
    if unity:
        samples["sub8"] = samples["sub32"]
    r, se = runner.ratio(samples)
    base = 1.2533 * 1.4826 / math.sqrt(5)
    expected = base * math.sqrt(2 if unity else 22) / 6
    return expect(
        r == (1 if unity else 3) and math.isclose(se, expected, rel_tol=1e-12),
        "ratio-error-exact",
    )


def measurement_case(
    runner: ModuleType, record: dict, folder: Path, alternating: bool
) -> tuple[int, str]:
    """Pin retained sample counts, total calls, and the first two measured orders."""
    entry = record["devices"][0]
    scenario, env = stub_fixture(runner, folder, [entry], 3)
    device = runner.Device(0, entry["name"], entry["device_id"])
    arms = runner.measurement_arms(STUB_FFMPEG, record["workload"])
    samples = runner.measure(device, record["workload"], arms, 5, env)
    calls = scenario.with_suffix(".calls").read_text().splitlines()
    forward = [runner.chain(size) for size in (None, 8, 32)]
    if alternating:
        return expect(
            calls[3:6] == forward and calls[6:9] == forward[::-1], "alternating-rounds"
        )
    return expect(
        len(calls) == 3 * (5 + 1) and all(len(v) == 5 for v in samples.values()),
        "warmup-discarded",
    )


def record_case(runner: ModuleType, record: dict, mode: str) -> tuple[int, str]:
    """Validate emitted lines and reject schema drift, noise, or rounded collapse."""
    entry, workload = record["devices"][0], record["workload"]
    device = runner.Device(0, entry["name"], entry["device_id"])
    ratios = {
        "emit": [(3.1145038167938934, 0), (5.8102189781021885, 0)],
        "noise": [(3, 0.6), (4.5, 0.8)],
        "collapse": [(3.0001, 0), (3.0002, 0)],
        "schema": [(3, 0), (5, 0)],
        "empty": [],
    }
    schema = runner.entry_schema("devices")
    if mode == "schema":
        schema["required"] = str
    output, errors = io.StringIO(), io.StringIO()
    devices = [] if mode == "empty" else [device]
    with (
        patch.object(runner, "driver_map", return_value={device[1:]: entry["driver"]}),
        patch.object(runner, "enumerate_devices", return_value=devices),
        patch.object(runner, "measure", return_value={}),
        patch.object(runner, "ratio", side_effect=ratios[mode]),
        patch.object(runner, "entry_schema", return_value=schema),
        patch.object(runner.time, "strftime", return_value="2026-11-12"),
        redirect_stdout(output),
        redirect_stderr(errors),
    ):
        count = runner.make_record(STUB_FFMPEG, STUB_FFMPEG, workload, 15)
    if mode != "emit":
        return expect(
            count == 0
            and not output.getvalue()
            and (mode == "empty" or "no gain on" in errors.getvalue()),
            mode,
        )
    line = output.getvalue()
    parsed = json.loads(line)
    runner.validate_record(dict(record, devices=[parsed]))
    return expect(
        count == 1
        and parsed == dict(entry, ratio_subgroup=3.115, ratio_tree=5.810)
        and '"ratio_tree": 5.810}' in line
        and not errors.getvalue(),
        "record-line-exact",
    )


def regression_cases(runner: ModuleType, record: dict, folder: Path) -> list[tuple]:
    """Collect independent regression assertions without hardware access."""
    id_call = partial(id_case, runner, folder, record)
    measured = partial(measurement_case, runner, record, folder)
    rows = (
        ("ratio-error-exact", partial(ratio_case, runner), "ratio-error-exact"),
        ("ratio-unity", partial(ratio_case, runner, True), "ratio-error-exact"),
        ("record-id-uppercase", partial(id_call, True), "PASS"),
        ("printed-id-uppercase", partial(id_call, False), "PASS"),
        ("warmup-discarded", partial(measured, False), "warmup"),
        ("alternating-rounds", partial(measured, True), "alternating"),
    )
    cases = [(n, call, 0, msg) for n, call, msg in rows]
    for mode in ("emit", "noise", "collapse", "schema", "empty"):
        call = partial(record_case, runner, record, mode)
        msg = "record-line-exact" if mode == "emit" else mode
        cases.append(("record-" + mode, call, 0, msg))
    for name, prefix, expected in (
        ("tree-line-comments", "keep\n    // subgroupAdd(d)\n    // combine\n", 5),
        ("tree-no-comment", "keep\n", 5),
        ("tree-block-comment", "keep\n    /* subgroupAdd(d)\n     * combine */\n", 5),
    ):
        start = runner.reduction_start(
            prefix + "    float sg = subgroupAdd(d);", len(prefix) + 15
        )
        cases.append((name, partial(expect, start == expected, name), 0, name))
    return cases


def enumeration_case(runner: ModuleType, record: dict, folder: Path, mode: str) -> str:
    """Exercise explicit end markers, failed probes, and the enumeration cap."""
    entry = record["devices"][0]
    line = f"Device 0 selected: {entry['name']} (discrete) ({entry['device_id']})"
    knobs = {
        "failed": dict(probe_index=1, probe_rc=1, probe_output="initialization failed"),
        "blank": dict(probe_rc=0, probe_output="unrelated output"),
        "none": dict(probe_rc=1, probe_output="No devices found: unavailable!"),
        "none-later": dict(probe_index=1, probe_output="No devices found"),
        "failed-valid": dict(
            probe_index=1, probe_rc=1, probe_output=line.replace("Device 0", "Device 1")
        ),
        "wrong-index": dict(probe_index=1, probe_output=line),
    }
    names = [entry] * (7 if mode == "seven" else 8 if mode == "cap" else 2)
    _, env = stub_fixture(runner, folder, names, 3, probe=knobs.get(mode))
    if mode == "timeout":
        with patch.object(
            runner, "run_ffmpeg", return_value=runner.RunResult(124, "timed out", "")
        ):
            return str(runner.enumerate_devices(STUB_FFMPEG, env))
    return str(len(runner.enumerate_devices(STUB_FFMPEG, env)))


def alias_case(runner: ModuleType, folder: Path, kind: str) -> tuple[int, str]:
    """Refuse each output alias before changing any source byte."""
    source, target = folder / (kind + ".glsl"), folder / (kind + "-out.glsl")
    original = (
        runner.ROOT / "ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl"
    ).read_bytes()
    source.write_bytes(original)
    if kind == "symlink":
        target.symlink_to(source)
    elif kind == "hardlink":
        os.link(source, target)
    else:
        target = source
    message = ""
    try:
        runner.tree_shader(source, target)
    except ValueError as exc:
        message = str(exc)
    return expect(
        message == "tree shader output aliases its input"
        and source.read_bytes() == original,
        "alias refused; bytes unchanged",
    )


def driver_parse_case(runner: ModuleType) -> tuple[int, str]:
    """Retain different device names sharing a PCI ID and unknown metadata."""
    text = "deviceID = 0xffff\nGPU0:\n deviceName = first\n deviceID = 0xABCD\n driverName = mesa\n driverInfo = 1.2\nGPU1:\n deviceName = second\n deviceID = 0xabcd\n driverName = other\n driverInfo = 3.4\nGPU2:\n deviceName = unknown\n deviceID = 0x1234\nGPU3:\n deviceName = incomplete\n"
    expected = {
        ("first", "0xabcd"): "mesa 1.2",
        ("second", "0xabcd"): "other 3.4",
        ("unknown", "0x1234"): "unknown",
    }
    return expect(
        runner.parse_drivers(text) == expected and runner.parse_drivers("") == {},
        "driver identities retained",
    )


def driver_case(
    runner: ModuleType, record: dict, folder: Path, mode: str
) -> tuple[int, str]:
    """Measure matching or unknown drivers and skip stale drivers before arms."""
    entry = record["devices"][0]
    scenario, env = stub_fixture(runner, folder, [entry], entry["ratio_subgroup"])
    driver = {
        "matching": entry["driver"],
        "stale": "changed driver",
        "unknown": "unknown",
    }[mode]
    with patch.object(
        runner, "driver_map", return_value={(entry["name"], entry["device_id"]): driver}
    ):
        code, message = runner.check(STUB_FFMPEG, record, 5, env)
    calls = scenario.with_suffix(".calls").read_text().splitlines()
    stale = mode == "stale"
    fragment = (
        "stale record: driver changed driver differs from recorded" if stale else "PASS"
    )
    timed = not calls if stale else len(calls) == 18
    return expect(code == (77 if stale else 0) and fragment in message and timed, mode)


def empty_main_case(runner: ModuleType) -> tuple[int, str]:
    """Return a CLI failure when record generation emits no device entries."""
    errors = io.StringIO()
    argv = ["mc-gain-smoke.py", "--make-record", "--tree-ffmpeg", str(STUB_FFMPEG)]
    with (
        patch.object(sys, "argv", argv),
        patch.dict(os.environ, FFMPEG_BIN=str(STUB_FFMPEG)),
        patch.object(runner, "resolve_ffmpeg_bin", return_value=STUB_FFMPEG),
        patch.object(runner, "make_record", return_value=0),
        redirect_stderr(errors),
    ):
        code = runner.main()
    return expect(
        code == 1 and "no device entries emitted" in errors.getvalue(),
        "empty main refused",
    )


def review_cases(runner: ModuleType, record: dict, folder: Path) -> list[tuple]:
    """Collect review regressions with named positive and boundary cases."""
    cases = []
    enumeration = partial(enumeration_case, runner, record, folder)
    for mode, rc, msg in (
        ("failed", 1, "device enumeration failed"),
        ("blank", 1, "device enumeration failed"),
        ("timeout", 1, "device enumeration failed"),
        ("none", 0, "0"),
        ("none-later", 1, "device enumeration failed"),
        ("failed-valid", 1, "device enumeration failed"),
        ("wrong-index", 1, "device enumeration failed"),
        ("seven", 0, "7"),
        ("cap", 1, "cap 8"),
    ):
        cases.append(("enumeration-" + mode, partial(enumeration, mode), rc, msg))
    for kind in ("same", "symlink", "hardlink"):
        call = partial(alias_case, runner, folder, kind)
        cases.append(("alias-" + kind, call, 0, "bytes unchanged"))
    for mode in ("matching", "stale", "unknown"):
        call = partial(driver_case, runner, record, folder, mode)
        cases.append(("driver-" + mode, call, 0, mode))
    call = partial(driver_parse_case, runner)
    cases.append(("driver-parser", call, 0, "driver identities retained"))
    call = partial(empty_main_case, runner)
    cases.append(("record-empty-main", call, 0, "empty main refused"))
    cases.append(("shipped-record", partial(runner.validate_record, record), 0, ""))
    return cases


def self_test(runner: ModuleType) -> int:
    """Run all canned and stub cases with a fixed asserted case count."""
    record = runner.load_record(Path(__file__).with_name("mc-gain-record.json"))
    with tempfile.TemporaryDirectory(prefix="mc-gain-") as directory:
        folder = Path(directory)
        entry = [record["devices"][0]]
        sub, tree = entry[0]["ratio_subgroup"], entry[0]["ratio_tree"]
        other = [dict(name="other", device_id="0xabcd")]
        wrong_id = [dict(entry[0], device_id="0xffff")]
        rows = (
            ("device-mismatch", entry, sub, True, 1, "device mismatch"),
            ("stub-subgroup", entry, sub, False, 0, "PASS"),
            ("stub-tree", entry, tree, False, 1, "FAIL"),
            ("stub-unguarded", record["unguarded"], 3, False, 77, "(#602)"),
            ("stub-no-device", [], 3, False, 77, "no guarded device: no Vulkan device"),
            ("stub-no-record", other, 3, False, 77, "no record"),
            ("stub-same-name-wrong-id", wrong_id, sub, False, 77, "no record"),
        )
        cases = (
            canned_cases(runner, record)
            + invalid_cases(runner, record, folder)
            + parsing_cases(runner, entry[0])
            + cli_cases(runner)
        )
        cases += [
            (n, partial(stub_case, runner, folder, record, ds, r, mis), rc, msg)
            for n, ds, r, mis, rc, msg in rows
        ]
        call = partial(shader_case, runner, folder)
        cases.append(("tree-shader", call, 0, "tree and unchanged exterior"))
        source = folder / "no-subgroup.glsl"
        source.write_text("float block_sad() { return 0; }", encoding="utf-8")
        call = partial(runner.tree_shader, source, folder / "bad.glsl")
        cases.append(("tree-refused", call, 1, "no subgroupAdd("))
        cases += regression_cases(runner, record, folder)
        cases += review_cases(runner, record, folder)
        passed = [run_case(*case) for case in cases]
        assert len(passed) == 96, f"unexpected case count: {len(passed)}"
        print(f"{sum(passed)}/{len(passed)} cases passed")
        return 0 if all(passed) else 1
