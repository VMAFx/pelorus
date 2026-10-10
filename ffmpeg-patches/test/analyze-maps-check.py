#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check vf_pelorus_analyze_vulkan's per-cell maps on hardware (issue #219, ADR-0177).

Reads the Pelorus blobs that FFmpeg's `showinfo` filter prints ("User Data="
hex after the Pelorus UUID line), decodes the header, the variance and banding
sections and their three maps, and applies the reader rules of `pel_blob_map()`
plus value checks. A payload in the zero-free carrier form (interop ABI 1.5,
ADR-0183: carrier UUID, then the COBS-encoded image, which NVENC streams carry)
is decoded as strictly as `pel_blob_unwrap()` does first. Called by
vulkan-format-matrix.sh and nvenc-udu-sei-smoke.sh; `--self-test` plants one
defect per rule and requires each to be rejected.

  analyze-maps-check.py maps LOG --grid COLSxROWS [--band-min X] [--band-max X]
  analyze-maps-check.py no-maps LOG --grid COLSxROWS
  analyze-maps-check.py contrast HIGH_LOG LOW_LOG --margin X
  analyze-maps-check.py same LOG_A LOG_B
  analyze-maps-check.py --self-test
"""

import math
import re
import struct
import sys

PELORUS_UUID = "e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64"
CARRIER_UUID = "3f9b37b8-fd9a-4621-920e-9b78b55cf9b5"
HEADER_FMT = "<8sHHIIHHQBBHHHII"
HEADER_BYTES = 48
SEC_BANDING, SEC_VARIANCE, SEC_COMPLEXITY = 1, 2, 128
BANDING_FMT = "<ffIIff"
VARIANCE_FMT = "<fffIIII"
# Variance of in-range samples is at most 0.25; P010/P012 storage reaches
# slightly above 1.0 after the descriptor scale, so allow a hair over.
VAR_MAX = 0.2502
MAX_BLOBS = 64


class MapError(Exception):
    """A blob or map that a reader must refuse."""


def uncobs(enc):
    """Strict COBS decode, the rules of pel_blob_unwrap(): no zero byte, no block
    past the end, no empty final block after a full one."""
    out, pos, prev = bytearray(), 0, 0
    while pos < len(enc):
        code = enc[pos]
        if code == 0 or code - 1 > len(enc) - pos - 1:
            raise MapError("carrier: code byte %d at %d runs past the end" % (code, pos))
        if pos + code == len(enc) and code == 1 and prev == 0xFF:
            raise MapError("carrier: empty block after a full one (non-canonical)")
        block = enc[pos + 1:pos + code]
        if 0 in block:
            raise MapError("carrier: zero byte in a block at %d" % pos)
        out += block
        pos += code
        if code != 0xFF and pos < len(enc):
            out.append(0)
        prev = code
    return bytes(out)


def blobs_from_log(text, forms=None):
    """Pelorus blob images (header onward) in the order showinfo printed them.
    A carrier payload is decoded; `forms`, when a list, receives "blob" or
    "carrier" per image."""
    blobs, pending = [], None
    for line in text.splitlines():
        if PELORUS_UUID in line or CARRIER_UUID in line:
            pending = "blob" if PELORUS_UUID in line else "carrier"
            continue
        match = re.search(r"User Data=([0-9a-f]*)\s*$", line)
        if match and pending:
            data = bytes.fromhex(match.group(1))
            blobs.append(uncobs(data) if pending == "carrier" else data)
            if forms is not None:
                forms.append(pending)
            pending = None
            if len(blobs) >= MAX_BLOBS:
                break
    return blobs


def section_table(image, count, hsize):
    table = {}
    for i in range(count):
        raw = image[hsize + 16 * i:hsize + 16 * i + 16]
        if len(raw) < 16:
            raise MapError("section directory truncated")
        sid, off, size, _minor = struct.unpack("<IIII", raw)
        if off % 8 or off + size > len(image):
            raise MapError("section %#x lies outside the blob" % sid)
        table[sid] = image[off:off + size]
    return table


def read_map(image, hdr, off, size, cells, elem):
    """The pel_blob_map() checks: exact size, 8-aligned, past dir[], inside total_size."""
    if size != cells * elem:
        raise MapError("map size %d != %d cells x %d" % (size, cells, elem))
    if off % 8:
        raise MapError("map offset %d is not 8-aligned" % off)
    if off < hdr["hsize"] + 16 * hdr["count"]:
        raise MapError("map offset %d overlaps the header or directory" % off)
    if off > hdr["total"] or size > hdr["total"] - off:
        raise MapError("map at %d+%d leaves total_size %d" % (off, size, hdr["total"]))
    return image[off:off + size]


def decode(image):
    """Header, frame scalars and maps of one analyze blob (maps None when absent)."""
    if len(image) < HEADER_BYTES:
        raise MapError("blob shorter than the header")
    f = struct.unpack(HEADER_FMT, image[:HEADER_BYTES])
    hdr = {"total": f[3], "count": f[5], "hsize": f[6], "cols": f[10], "rows": f[11]}
    if f[0] != b"PELOR1\0\0" or f[1] != 1 or hdr["hsize"] != HEADER_BYTES:
        raise MapError("bad magic, ABI major or header size")
    if hdr["total"] != len(image):
        raise MapError("total_size %d != blob length %d" % (hdr["total"], len(image)))
    table = section_table(image, hdr["count"], hdr["hsize"])
    if SEC_BANDING not in table or SEC_VARIANCE not in table:
        raise MapError("banding or variance section missing")
    band = struct.unpack(BANDING_FMT, table[SEC_BANDING][:24])
    var = struct.unpack(VARIANCE_FMT, table[SEC_VARIANCE][:28])
    out = {"hdr": hdr, "gvar": var[0], "gedge": var[1], "maps": None,
           "offsets": (band[2], band[3], var[3], var[4], var[5], var[6])}
    if not any(out["offsets"]):
        return out
    cells = hdr["cols"] * hdr["rows"]
    out["maps"] = {
        "band": list(read_map(image, hdr, band[2], band[3], cells, 1)),
        "var": list(struct.unpack("<%df" % cells, read_map(image, hdr, var[3], var[4], cells, 4))),
        "edge": list(read_map(image, hdr, var[5], var[6], cells, 1)),
    }
    return out


def mean(values):
    return sum(values) / len(values)


def map_problems(blob, grid):
    """Grid, presence and value rules for one blob that must carry maps."""
    hdr, maps = blob["hdr"], blob["maps"]
    if (hdr["cols"], hdr["rows"]) != grid:
        return ["grid %dx%d != expected %dx%d" % ((hdr["cols"], hdr["rows"]) + grid)]
    if maps is None:
        return ["no per-cell maps (all map offsets and sizes are zero)"]
    errs = []
    if not all(math.isfinite(v) and 0.0 <= v <= VAR_MAX for v in maps["var"]):
        errs.append("variance map element outside [0, %g]" % VAR_MAX)
    elif abs(mean(maps["var"]) - blob["gvar"]) > 1e-7 + 1e-4 * blob["gvar"]:
        errs.append("variance map mean %.9g != global_variance %.9g"
                    % (mean(maps["var"]), blob["gvar"]))
    if abs(mean(maps["edge"]) / 255.0 - blob["gedge"]) > 0.5 / 255.0 + 1e-6:
        errs.append("edge map mean %.6f != edge_density %.6f"
                    % (mean(maps["edge"]) / 255.0, blob["gedge"]))
    return errs


def load(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        blobs = blobs_from_log(fh.read())
    if not blobs:
        raise MapError("%s: no Pelorus blob in the showinfo log" % path)
    return [decode(b) for b in blobs]


def summary(blob):
    maps = blob["maps"]
    return "grid %dx%d band %.3f var %.6f edge %.3f" % (
        blob["hdr"]["cols"], blob["hdr"]["rows"], mean(maps["band"]) / 255.0,
        mean(maps["var"]), mean(maps["edge"]) / 255.0)


def parse_grid(text):
    cols, rows = text.lower().split("x")
    return int(cols), int(rows)


def cmd_maps(path, grid, band_min, band_max):
    errs = []
    for idx, blob in enumerate(load(path)):
        errs += ["frame %d: %s" % (idx, e) for e in map_problems(blob, grid)]
        if blob["maps"] is not None and not errs:
            band = mean(blob["maps"]["band"]) / 255.0
            if not band_min <= band <= band_max:
                errs.append("frame %d: banding map mean %.3f outside [%g, %g]"
                            % (idx, band, band_min, band_max))
            print("%s: %s" % (path, summary(blob)))
    return errs


def cmd_no_maps(path, grid):
    errs = []
    for idx, blob in enumerate(load(path)):
        hdr = blob["hdr"]
        if (hdr["cols"], hdr["rows"]) != grid:
            errs.append("frame %d: grid %dx%d != expected %dx%d"
                        % ((idx, hdr["cols"], hdr["rows"]) + grid))
        if any(blob["offsets"]):
            errs.append("frame %d: maps=0 still carries map offsets %s"
                        % (idx, blob["offsets"]))
    return errs


def contrast_problems(a, b, margin):
    """Blob `a` (banded) must score a banding-map mean at least `margin` above `b`."""
    if a["maps"] is None or b["maps"] is None:
        return ["contrast needs maps in both blobs"]
    ma, mb = mean(a["maps"]["band"]) / 255.0, mean(b["maps"]["band"]) / 255.0
    print("banding map mean: %.3f vs %.3f" % (ma, mb))
    if ma < mb + margin:
        return ["banding map mean %.3f is not %.3f above %.3f" % (ma, margin, mb)]
    return []


def cmd_contrast(high, low, margin):
    return contrast_problems(load(high)[0], load(low)[0], margin)


def cmd_same(path_a, path_b):
    return same_problems(load(path_a)[0], load(path_b)[0])


def same_problems(a, b):
    """Two formats of one picture give the same maps within quantisation noise."""
    if a["maps"] is None or b["maps"] is None:
        return ["same needs maps in both blobs"]
    errs = []
    for key, scale, tol in (("band", 255.0, 0.05), ("var", 1.0, None), ("edge", 255.0, 0.02)):
        x, y = mean(a["maps"][key]) / scale, mean(b["maps"][key]) / scale
        limit = tol if tol is not None else max(1e-6, abs(x) * 0.05)
        if not abs(x - y) <= limit:
            errs.append("%s map mean %.6f vs %.6f (tolerance %g)" % (key, x, y, limit))
    return errs


# ------------------------------------------------------------------ self-test

def pack_blob(cols, rows, band, var, edge, maps=True):
    """A blob laid out the way pelorus_analyze_maps.h packs it."""
    cells = cols * rows
    end = 48 + 3 * 16 + 32 + 24 + 16
    offs = [0] * 6
    if maps:
        b_off = end
        v_off = (b_off + cells + 7) & ~7
        e_off = (v_off + 4 * cells + 7) & ~7
        offs = [b_off, cells, v_off, 4 * cells, e_off, cells]
        end = e_off + cells
    gvar = sum(var) / cells
    gedge = sum(edge) / cells / 255.0
    image = bytearray(end)
    hdr = struct.pack(HEADER_FMT, b"PELOR1\0\0", 1, 4, end, 131, 3, 48, 0, 0, 8,
                      cols, rows, 0, 0x41524C50, 0)
    image[0:48] = hdr
    for i, (sid, off, size) in enumerate(((SEC_VARIANCE, 96, 28), (SEC_BANDING, 128, 24),
                                          (SEC_COMPLEXITY, 152, 16))):
        image[48 + 16 * i:64 + 16 * i] = struct.pack("<IIII", sid, off, size, 4)
    image[96:124] = struct.pack(VARIANCE_FMT, gvar, gedge, gedge, offs[2], offs[3], offs[4],
                                offs[5])
    image[128:152] = struct.pack(BANDING_FMT, 0.5, 0.5, offs[0], offs[1], 0.0, 0.0)
    if maps:
        image[offs[0]:offs[0] + cells] = bytes(band)
        image[offs[2]:offs[2] + 4 * cells] = struct.pack("<%df" % cells, *var)
        image[offs[4]:offs[4] + cells] = bytes(edge)
    return bytes(image)


def cobs(data):
    """COBS as pel_blob_carrier_encode() writes it (no empty block after a final full one)."""
    out, idx, code = bytearray([0]), 0, 1
    for i, byte in enumerate(data):
        if byte:
            out.append(byte)
            code += 1
        if not byte or (code == 0xFF and i + 1 < len(data)):
            out[idx] = code
            idx, code = len(out), 1
            out.append(0)
    out[idx] = code
    return bytes(out)


def as_log(image, carrier=False):
    uuid, data = (CARRIER_UUID, cobs(image)) if carrier else (PELORUS_UUID, image)
    return ("[Parsed_showinfo_5 @ 0x1] side data - User Data Unregistered SEI message: "
            "UUID=%s\n[Parsed_showinfo_5 @ 0x1] User Data=%s\n" % (uuid, data.hex()))


def carrier_self_test(good):
    """The carrier form decodes to the same image; corrupt stuffing is refused."""
    failures, forms = [], []
    if blobs_from_log(as_log(good, carrier=True), forms) != [good] or forms != ["carrier"]:
        failures.append("carrier form of a valid blob not decoded to the blob")
    if cobs(bytes(range(1, 255))) != b"\xff" + bytes(range(1, 255)):
        failures.append("cobs: a final full block got an empty block after it")
    enc = cobs(good)
    for label, bad in (("zero byte in the carrier", enc[:5] + b"\x00" + enc[6:]),
                       ("block past the end of the carrier", enc + b"\x09"),
                       ("empty block after a final full block",
                        b"\xff" + bytes(range(1, 255)) + b"\x01")):
        try:
            blobs_from_log("UUID=%s\nUser Data=%s\n" % (CARRIER_UUID, bad.hex()))
        except MapError:
            continue
        failures.append("planted defect accepted: " + label)
    return failures


def self_test():
    band, var, edge = [200, 210, 220, 230, 240, 250], [1e-4] * 6, [10, 20, 30, 40, 50, 60]
    good = pack_blob(3, 2, band, var, edge)
    ok = decode(blobs_from_log(as_log(good))[0])
    failures = []
    if map_problems(ok, (3, 2)):
        failures.append("valid blob rejected: %s" % map_problems(ok, (3, 2)))

    def rejected(label, image, grid=(3, 2)):
        try:
            problems = map_problems(decode(image), grid)
        except MapError:
            return
        if not problems:
            failures.append("planted defect accepted: " + label)

    rejected("no maps (the pre-#219 output)", pack_blob(3, 2, band, var, edge, maps=False))
    rejected("wrong grid", good, grid=(2, 3))
    bad = bytearray(good)
    bad[12:16] = struct.pack("<I", len(good) + 8)  # total_size past the received bytes
    rejected("total_size past the blob", bytes(bad))
    bad = bytearray(good)
    bad[96 + 12:96 + 16] = struct.pack("<I", struct.unpack("<I", good[108:112])[0] + 4)
    rejected("misaligned variance map", bytes(bad))
    bad = bytearray(good)
    bad[96 + 16:96 + 20] = struct.pack("<I", 20)
    rejected("variance map size not cells x 4", bytes(bad))
    bad = bytearray(good)
    bad[128 + 8:128 + 12] = struct.pack("<I", 8)
    rejected("banding map over the directory", bytes(bad))
    rejected("NaN variance", pack_blob(3, 2, band, [float("nan")] + var[1:], edge))
    rejected("variance above 0.25", pack_blob(3, 2, band, [0.3] + var[1:], edge))
    bad = bytearray(good)
    bad[96:100] = struct.pack("<f", 0.5)
    rejected("variance map mean != global_variance", bytes(bad))
    bad = bytearray(good)
    bad[100:104] = struct.pack("<f", 0.9)
    rejected("edge map mean != edge_density", bytes(bad))
    if blobs_from_log("User Data=00ff\n"):
        failures.append("a foreign SEI was read as a Pelorus blob")
    failures += carrier_self_test(good)
    low = decode(pack_blob(3, 2, [10] * 6, var, edge))
    if contrast_problems(ok, low, 0.25):
        failures.append("valid contrast rejected")
    for label, x, y in (("contrast inverted", low, ok), ("contrast equal", ok, ok)):
        if not contrast_problems(x, y, 0.25):
            failures.append("planted defect accepted: " + label)
    if same_problems(ok, ok):
        failures.append("identical maps reported as different")
    if not same_problems(ok, low):
        failures.append("planted defect accepted: different banding maps reported as same")
    for failure in failures:
        print("FAIL: " + failure, file=sys.stderr)
    print("analyze-maps-check self-test: %s" % ("FAIL" if failures else "PASS"))
    return 1 if failures else 0


def main(argv):
    if argv[:1] == ["--self-test"]:
        return self_test()
    try:
        if len(argv) >= 4 and argv[0] in ("maps", "no-maps") and argv[2] == "--grid":
            grid = parse_grid(argv[3])
            opts = dict(zip(argv[4::2], argv[5::2]))
            if argv[0] == "no-maps":
                errs = cmd_no_maps(argv[1], grid)
            else:
                errs = cmd_maps(argv[1], grid, float(opts.get("--band-min", 0.0)),
                                float(opts.get("--band-max", 1.0)))
        elif len(argv) == 5 and argv[0] == "contrast" and argv[3] == "--margin":
            errs = cmd_contrast(argv[1], argv[2], float(argv[4]))
        elif len(argv) == 3 and argv[0] == "same":
            errs = cmd_same(argv[1], argv[2])
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (MapError, OSError, ValueError, struct.error) as exc:
        errs = [str(exc)]
    for err in errs:
        print("FAIL: " + err, file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
