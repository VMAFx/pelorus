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
        rc, message = value if type(value) is tuple else (0, str(value))
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
    mixed = [
        (77, "SKIP: inconclusive: noisy"),
        (78, "SKIP: no guarded device"),
        (0, "PASS"),
    ]
    expected = (77, "SKIP: no guarded device\nPASS\nSKIP: inconclusive: noisy")
    checks = (
        ("stats-outlier", runner.stats([1, 2, 3, 4, 100]) == values),
        ("overall-inconclusive-last", runner.summarize(mixed) == expected),
        ("separation-2-se", not runner.separated(3.0, 0.0, 4.0, 0.5)),
    )
    cases += [(n, partial(expect, ok, n), 0, n) for n, ok in checks]
    nonpositive = dict(samples, sub8=[9.0] * 5)
    overall = (
        ("overall-fail-pass", [(1, "FAIL"), (0, "PASS")], 1, "FAIL"),
        ("overall-pass", [(0, "PASS")], 0, "PASS"),
        ("overall-unguarded", [(78, "no guarded device")], 77, "no guarded device"),
    )
    cases += [
        (n, partial(runner.summarize, data), rc, msg) for n, data, rc, msg in overall
    ]
    cases.append(("ratio-orientation", partial(runner.judge, samples, entry), 1, "R=5"))
    call = partial(runner.judge, nonpositive, entry)
    cases.append(("non-positive", call, 77, "non-positive net time"))
    for arm in ("sub8", "sub32"):
        call = partial(runner.judge, dict(samples, **{arm: [10.0] * 5}), entry)
        cases.append((f"zero-net-{arm}", call, 77, "non-positive net time"))
    return cases


def invalid_mutations() -> tuple[tuple[str, Callable, str], ...]:
    """Describe independent invalid records and their required diagnostics."""
    bounds = "invalid workload bounds"
    return (
        ("missing-key", lambda b: b.pop("workload"), "missing or extra key"),
        ("extra-key", lambda b: b.update(extra=True), "missing or extra key"),
        (
            "bad-ratio",
            lambda b: b["devices"][0].update(
                ratio_tree=b["devices"][0]["ratio_subgroup"]
            ),
            "ratio_tree must exceed positive ratio_subgroup",
        ),
        ("bad-id", lambda b: b["devices"][0].update(device_id="2684"), "bad device_id"),
        ("runs-four", lambda b: b["devices"][0].update(runs=4), "invalid runs"),
        ("bsize-seven", lambda b: b["workload"].update(reduction_bsize=7), bounds),
        ("bsize-33", lambda b: b["workload"].update(fetch_bsize=33), bounds),
        ("rate-zero", lambda b: b["workload"].update(rate=0), bounds),
        ("frames-zero", lambda b: b["workload"].update(frames=0), bounds),
        (
            "no-issue",
            lambda b: b["unguarded"][0].update(reason="slower"),
            "unguarded reason must name #N",
        ),
        (
            "duplicate",
            lambda b: b["devices"].append(b["devices"][0]),
            "duplicate device",
        ),
        (
            "duplicate-id-case",
            lambda b: b["devices"].extend(
                dict(b["devices"][0], device_id=i) for i in ("0x13c0", "0x13C0")
            ),
            "duplicate device",
        ),
        (
            "equal-workload-bsize",
            lambda b: b["workload"].update(
                reduction_bsize=b["workload"]["fetch_bsize"]
            ),
            bounds,
        ),
        (
            "date-nonpadded",
            lambda b: b["devices"][0].update(measured="2026-1-12"),
            "",
        ),
        (
            "date-basic",
            lambda b: b["devices"][0].update(measured="20260112"),
            "invalid runs or measured date",
        ),
    )


def invalid_cases(runner: ModuleType, record: dict, folder: Path) -> list[tuple]:
    """Exercise invalid records and the accepted lower run-count boundary."""
    cases = []
    for name, mutate, fragment in invalid_mutations():
        bad = json.loads(json.dumps(record))
        mutate(bad)
        path = folder / (name + ".json")
        path.write_text(json.dumps(bad), encoding="utf-8")
        cases.append(
            (
                name,
                partial(runner.load_record, path),
                1,
                f"record invalid: {path}: {fragment}",
            )
        )
    good = json.loads(json.dumps(record))
    good["devices"][0]["runs"] = 5
    path = folder / "runs-five.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    cases.append(("runs-five", partial(runner.load_record, path), 0, ""))
    return cases


def cli_cases(runner: ModuleType) -> list[tuple]:
    """Check CLI argument refusals before the missing-binary skip."""
    path = getattr(runner, "__file__", None)
    path = Path(path) if path else runner.ROOT / "ffmpeg-patches/test/mc-gain-smoke.py"
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
        call = partial(
            expect, result.returncode == expected and fragment in message, detail
        )
        cases.append((name, call, 0, fragment))
    return cases


def stub_fixture(
    runner: ModuleType,
    folder: Path,
    names: list[dict],
    r: float,
    mismatch: bool = False,
) -> tuple[Path, dict[str, str]]:
    """Write a stub scenario and retain the inherited Git isolation barrier."""
    scenario = folder / "scenario.json"
    times = {runner.chain(None): 1, runner.chain(8): 1 + r, runner.chain(32): 2}
    data = dict(devices=names, times=times, mismatch=mismatch)
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


def ratio_case(runner: ModuleType) -> tuple[int, str]:
    """Pin R and its propagated error with spread in every arm."""
    samples = {
        "passthrough": [1, 2, 3, 4, 5],
        "sub8": [18, 18, 21, 24, 24],
        "sub32": [7, 8, 9, 10, 11],
    }
    r, se = runner.ratio(samples)
    base = 1.2533 * 1.4826 / math.sqrt(5)
    expected = 3 * base * math.sqrt(10 / 18**2 + 2 / 6**2)
    return expect(
        r == 3 and math.isclose(se, expected, rel_tol=1e-12), "ratio-error-exact"
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


def record_case(runner: ModuleType, entry: dict, separated: bool) -> tuple[int, str]:
    """Verify emitted record formatting and refusal at 1.5 combined standard errors."""
    device = runner.Device(0, entry["name"], entry["device_id"])
    ratios = [(3.1145038167938934, 0), (5.8102189781021885, 0)]
    if not separated:
        ratios = [(3, 0.6), (4.5, 0.8)]
    output, errors = io.StringIO(), io.StringIO()
    with (
        patch.object(
            runner, "driver_map", return_value={device.device_id: entry["driver"]}
        ),
        patch.object(runner, "enumerate_devices", return_value=[device]),
        patch.object(runner, "measure", return_value={}),
        patch.object(runner, "ratio", side_effect=ratios),
        patch.object(runner.time, "strftime", return_value="2026-11-12"),
        redirect_stdout(output),
        redirect_stderr(errors),
    ):
        runner.make_record(
            STUB_FFMPEG, STUB_FFMPEG, {"reduction_bsize": 8, "fetch_bsize": 32}, 15
        )
    if not separated:
        return expect(
            not output.getvalue() and "no gain on" in errors.getvalue(),
            "separation-1.5-se",
        )
    expected = (
        '{"name": "NVIDIA GeForce RTX 4090", "device_id": "0x2684", '
        '"driver": "NVIDIA 620.70.06", "measured": "2026-11-12", "runs": 15, '
        '"ratio_subgroup": 3.115, "ratio_tree": 5.810}\n'
    )
    return expect(
        output.getvalue() == expected and not errors.getvalue(), "record-line-exact"
    )


def regression_cases(runner: ModuleType, record: dict, folder: Path) -> list[tuple]:
    """Collect independent regression assertions without hardware access."""
    entry = record["devices"][0]
    cases = [
        ("ratio-error-exact", partial(ratio_case, runner), 0, "ratio-error-exact"),
        (
            "record-id-uppercase",
            partial(id_case, runner, folder, record, True),
            0,
            "PASS",
        ),
        (
            "printed-id-uppercase",
            partial(id_case, runner, folder, record, False),
            0,
            "PASS",
        ),
        (
            "warmup-discarded",
            partial(measurement_case, runner, record, folder, False),
            0,
            "warmup",
        ),
        (
            "alternating-rounds",
            partial(measurement_case, runner, record, folder, True),
            0,
            "alternating",
        ),
        (
            "record-line-exact",
            partial(record_case, runner, entry, True),
            0,
            "record-line-exact",
        ),
        (
            "separation-1.5-se",
            partial(record_case, runner, entry, False),
            0,
            "separation-1.5-se",
        ),
    ]
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
        passed = [run_case(*case) for case in cases]
        assert len(passed) == 67, f"unexpected case count: {len(passed)}"
        print(f"{sum(passed)}/{len(passed)} cases passed")
        return 0 if all(passed) else 1
