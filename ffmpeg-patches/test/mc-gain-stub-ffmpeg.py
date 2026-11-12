# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Emulate device enumeration and benchmark timing for the mc gain self-test.

The runner starts this file through the Python interpreter.
"""

import argparse
import json
import os
from pathlib import Path
import sys


def main() -> int:
    """Print the selected device and log each timed arm from the scenario."""
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("-init_hw_device", required=True)
    parser.add_argument("-vf", default=None)
    args, _ = parser.parse_known_args()
    path = Path(os.environ["MC_GAIN_STUB"])
    scenario = json.loads(path.read_text(encoding="utf-8"))
    index = int(args.init_hw_device.split(":")[-1])
    enumeration = args.vf is None
    if enumeration:
        with path.with_suffix(".log").open("a", encoding="utf-8") as log:
            log.write(str(index) + "\n")
    if index >= len(scenario["devices"]):
        print(f"Unable to find device with index {index}!")
        print(f"Device {index} selected: decoy (unknown) (0xffff)")
        return 0
    device = scenario["devices"][index]
    name = (
        "wrong device"
        if scenario.get("mismatch") and not enumeration
        else device["name"]
    )
    print(f"Device {index} selected: {name} (discrete) ({device['device_id']})")
    if not enumeration:
        arm = args.vf
        with path.with_suffix(".calls").open("a", encoding="utf-8") as log:
            log.write(arm + "\n")
        value = scenario["times"][arm]
        print(f"bench: utime=0.1s stime=0.1s rtime={value}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
