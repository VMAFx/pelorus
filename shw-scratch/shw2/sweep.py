#!/usr/bin/env python3
"""shw-2 filter x format x option sweep with Khronos validation (core + sync).

Runs every Pelorus Vulkan filter's documented option set on one GPU, in the
stock-FFmpeg device mode whose bare upload/download round trip is exact, and
checks: exit status, frame count, every validation diagnostic (verbatim),
bit-exact pass-through planes, identity for analyzers/no-ops, documented
bit-identical variants, P010 padding bits, and side-data/metadata presence.

usage: python3 sweep.py <label> <vulkan-device-spec> [--gpuav] [--only FILTER,...]
       [--formats f1,f2] [--rows substr]
"""
import argparse
import array
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

FFM = r"C:/tmp/pel/prefix/bin/ffmpeg.exe"
RES = Path(r"C:/tmp/pel/shw-results/shw-2")
KNOWN_STOCK = {
    # Proven in steps 1-3/9 with a bare hwupload,hwdownload (no Pelorus code):
    "VUID-VkFormatProperties2-pNext-pNext",  # Intel driver zeroes VkFormatProperties3 header; FFmpeg reuses chain
    "VUID-VkHostImageLayoutTransitionInfo-oldLayout-09230",  # FFmpeg host transition from TRANSFER_DST_OPTIMAL
    # Layer configuration notices printed once at vkCreateInstance when GPU-AV is on:
    "VALIDATION-SETTINGS",
    "WARNING-Setting-Limit-Adjusted",
}
W, H = 160, 96
FRAMES = 5
SRC = f"testsrc2=size={W}x{H}:rate=10,noise=alls=10:allf=t:all_seed=7"
MAIN_FORMATS = ["yuv420p", "nv12", "yuv420p10le", "p010le"]
EXTRA_FORMATS = ["yuv420p12le", "p012le", "yuv444p", "yuv422p10le"]


def planes(fmt, w, h):
    """(name, width, height, bytes_per_sample, components, shift, is_luma)"""
    cw, ch = (w + 1) // 2, (h + 1) // 2
    t = {
        "yuv420p": [("Y", w, h, 1, 1, 0), ("U", cw, ch, 1, 1, 0), ("V", cw, ch, 1, 1, 0)],
        "yuv420p10le": [("Y", w, h, 2, 1, 0), ("U", cw, ch, 2, 1, 0), ("V", cw, ch, 2, 1, 0)],
        "yuv420p12le": [("Y", w, h, 2, 1, 0), ("U", cw, ch, 2, 1, 0), ("V", cw, ch, 2, 1, 0)],
        "yuv444p": [("Y", w, h, 1, 1, 0), ("U", w, h, 1, 1, 0), ("V", w, h, 1, 1, 0)],
        "yuv422p10le": [("Y", w, h, 2, 1, 0), ("U", cw, h, 2, 1, 0), ("V", cw, h, 2, 1, 0)],
        "nv12": [("Y", w, h, 1, 1, 0), ("UV", cw, ch, 1, 2, 0)],
        "p010le": [("Y", w, h, 2, 1, 6), ("UV", cw, ch, 2, 2, 6)],
        "p012le": [("Y", w, h, 2, 1, 4), ("UV", cw, ch, 2, 2, 4)],
    }[fmt]
    return t


def depth(fmt):
    return {"yuv420p10le": 10, "p010le": 10, "yuv422p10le": 10, "yuv420p12le": 12, "p012le": 12}.get(fmt, 8)


def frame_bytes(fmt, w, h):
    return sum(pw * ph * bps * comps for _, pw, ph, bps, comps, _ in planes(fmt, w, h))


def split_planes(raw, fmt, w, h, nframes):
    """-> list over frames of list of (name, samples(list-like), shift, bps)"""
    out, off = [], 0
    for _ in range(nframes):
        fr = []
        for name, pw, ph, bps, comps, shift in planes(fmt, w, h):
            n = pw * ph * comps * bps
            chunk = raw[off:off + n]
            off += n
            if bps == 1:
                s = chunk
            else:
                s = array.array("H")
                s.frombytes(chunk)
            fr.append((name, s, shift, bps))
        out.append(fr)
    return out


def plane_stats(a, b, shift):
    if a == b:
        return 0, 0, math.inf
    diffs = [abs((x >> shift) - (y >> shift)) for x, y in zip(a, b)]
    nd = sum(1 for d in diffs if d)
    mse = sum(d * d for d in diffs) / len(diffs)
    return nd, max(diffs), mse


def parse_diags(text):
    """Return list of (id, block) for each validation-layer message in text."""
    msgs = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^Validation (Error|Warning|Information|Performance Warning): \[ ([^\]]+) \]", lines[i])
        if m:
            block = [lines[i]]
            i += 1
            while i < len(lines) and lines[i].strip() != "" and not lines[i].startswith("Validation "):
                block.append(lines[i])
                i += 1
            msgs.append((m.group(2).strip(), "\n".join(block)))
        else:
            i += 1
    return msgs


class Sweep:
    def __init__(self, label, spec, gpuav, sync=True):
        self.label, self.spec = label, spec
        self.out = RES / ("10-sweep-gpuav" if gpuav else "10-sweep") / label
        self.out.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ)
        self.env.update({
            "VK_LAYER_PATH": r"C:\msys64\ucrt64\bin",
            "VK_INSTANCE_LAYERS": "VK_LAYER_KHRONOS_validation",
        })
        if sync:
            self.env["VK_LAYER_VALIDATE_SYNC"] = "1"
        if gpuav:
            self.env["VK_LAYER_GPUAV_ENABLE"] = "1"
        self.results = []
        self.diag_texts = {}  # id -> first verbatim block (normalized)
        self.raw_cache = {}

    def ffmpeg(self, tag, vf, fmt, w=W, h=H, frames=FRAMES, src=None, loglevel="warning", raw=True):
        src = src or SRC.replace(f"{W}x{H}", f"{w}x{h}")
        outraw = self.out / f"{tag}.raw"
        cmd = [FFM, "-hide_banner", "-nostdin", "-loglevel", loglevel, "-y",
               "-init_hw_device", f"vulkan=vk:{self.spec}", "-filter_hw_device", "vk",
               "-f", "lavfi", "-i", src, "-frames:v", str(frames), "-vf", vf, "-fps_mode", "passthrough"]
        cmd += ["-f", "rawvideo", str(outraw)] if raw else ["-f", "null", "-"]
        t0 = time.time()
        p = subprocess.run(cmd, env=self.env, capture_output=True)
        dt = time.time() - t0
        so = p.stdout.decode("utf-8", "replace")
        se = p.stderr.decode("utf-8", "replace")
        (self.out / f"{tag}.stdout").write_text(so, encoding="utf-8")
        (self.out / f"{tag}.stderr").write_text(se, encoding="utf-8")
        (self.out / f"{tag}.cmd").write_text(subprocess.list2cmdline(cmd), encoding="utf-8")
        data = outraw.read_bytes() if (raw and outraw.exists() and p.returncode == 0) else None
        return p.returncode, so, se, data, dt

    def baseline(self, fmt, w=W, h=H, frames=FRAMES, src=None):
        key = (fmt, w, h, frames, src)
        if key not in self.raw_cache:
            tag = f"base-{fmt}-{w}x{h}-{frames}" + ("-alt" if src else "")
            rc, so, se, data, _ = self.ffmpeg(tag, f"format={fmt},hwupload,hwdownload,format={fmt}", fmt, w, h,
                                              frames, src)
            # CPU reference: exact data path proof for this device mode.
            src2 = src or SRC.replace(f"{W}x{H}", f"{w}x{h}")
            cpu = self.out / f"cpu-{fmt}-{w}x{h}-{frames}" f"{'-alt' if src else ''}.raw"
            subprocess.run([FFM, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src2,
                            "-frames:v", str(frames), "-vf", f"format={fmt}", "-f", "rawvideo", str(cpu)],
                           capture_output=True)
            exact = data is not None and cpu.exists() and cpu.read_bytes() == data
            self.raw_cache[key] = (data, exact, rc)
            self.record(dict(filter="(baseline)", row="hwupload,hwdownload", fmt=fmt, size=f"{w}x{h}",
                             rc=rc, checks={"roundtrip_exact_vs_cpu": exact}, diags=self.diags_of(so, se),
                             ok=rc == 0 and exact))
        return self.raw_cache[key]

    def diags_of(self, so, se):
        ids = {}
        for mid, block in parse_diags(so) + parse_diags(se):
            ids[mid] = ids.get(mid, 0) + 1
            norm = re.sub(r"0x[0-9a-fA-F]{6,}", "0x…", block)
            self.diag_texts.setdefault(mid, norm)
        return ids

    def record(self, r):
        self.results.append(r)
        flag = "PASS" if r["ok"] else "FAIL"
        extra = {k: v for k, v in r["checks"].items() if v is not True}
        diag = {k: v for k, v in r["diags"].items() if k not in KNOWN_STOCK}
        print(f"{flag} {self.label} {r['fmt']:<12} {r['filter']:<30} {r['row'][:70]:<70} "
              f"{'' if not extra else extra} {'' if not diag else 'NEW-DIAG ' + str(diag)}", flush=True)

    def run_row(self, filt, row, expects, fmt, w=W, h=H, frames=FRAMES, src=None, same_as=None, pre="", post=""):
        base, base_exact, _ = self.baseline(fmt, w, h, frames, src)
        tagrow = re.sub(r"[^A-Za-z0-9_.=-]+", "_", row)[:80]
        tag = f"{filt}-{fmt}-{w}x{h}-{tagrow}"
        need_info = any(e.startswith(("sidedata:", "nosidedata:", "log:")) for e in expects)
        chain = f"format={fmt},hwupload,{pre}{filt}{('=' + row) if row else ''},hwdownload,format={fmt}{post}"
        if need_info:
            chain += ",showinfo"
        rc, so, se, data, dt = self.ffmpeg(tag, chain, fmt, w, h, frames, src,
                                           loglevel="info" if need_info else "warning")
        checks = {"exit0": rc == 0}
        diags = self.diags_of(so, se)
        checks["no_new_diag"] = all(k in KNOWN_STOCK for k in diags)
        info = {"sec": round(dt, 2)}
        if data is not None:
            checks["frames"] = len(data) == frame_bytes(fmt, w, h) * frames
            if checks["frames"] and base is not None:
                A = split_planes(data, fmt, w, h, frames)
                B = split_planes(base, fmt, w, h, frames)
                per_plane = {}
                for fa, fb in zip(A, B):
                    for (name, sa, shift, bps), (_, sb, _, _) in zip(fa, fb):
                        nd, mx, mse = plane_stats(sa, sb, shift)
                        acc = per_plane.setdefault(name, [0, 0, 0.0, 0])
                        acc[0] += nd
                        acc[1] = max(acc[1], mx)
                        acc[2] += mse if mse != math.inf else 0.0
                        acc[3] += 1
                peak = (1 << depth(fmt)) - 1
                summary = {}
                for name, (nd, mx, mse_sum, n) in per_plane.items():
                    mse = mse_sum / n
                    summary[name] = {"diff": nd, "max": mx,
                                     "psnr": "inf" if nd == 0 else round(10 * math.log10(peak * peak / mse), 2)}
                info["planes"] = summary
                luma = summary["Y"]
                chroma = [v for k, v in summary.items() if k != "Y"]
                for e in expects:
                    if e == "identity":
                        checks["identity"] = all(v["diff"] == 0 for v in summary.values())
                    elif e == "chroma_exact":
                        checks["chroma_exact(psnr u/v inf)"] = all(v["diff"] == 0 for v in chroma)
                    elif e == "luma_exact":
                        checks["luma_exact"] = luma["diff"] == 0
                    elif e == "luma_changed":
                        checks["luma_changed"] = luma["diff"] > 0
                    elif e == "chroma_changed":
                        checks["chroma_changed"] = any(v["diff"] > 0 for v in chroma)
                # shifted-store contract: P010/P012 low padding bits stay zero
                if fmt in ("p010le", "p012le"):
                    sh = 6 if fmt == "p010le" else 4
                    words = array.array("H")
                    words.frombytes(data)
                    checks["padding_bits_zero"] = not any(v & ((1 << sh) - 1) for v in words)
                if fmt in ("yuv420p10le", "yuv420p12le", "yuv422p10le"):
                    words = array.array("H")
                    words.frombytes(data)
                    checks["no_overflow_bits"] = max(words) <= (1 << depth(fmt)) - 1
            if same_as is not None:
                other = self.out / f"{filt}-{fmt}-{w}x{h}-{re.sub(r'[^A-Za-z0-9_.=-]+', '_', same_as)[:80]}.raw"
                checks[f"bit_identical_to[{same_as}]"] = other.exists() and other.read_bytes() == data
        for e in expects:
            if e.startswith("sidedata:"):
                checks[e] = re.search(e.split(":", 1)[1], se) is not None
            elif e.startswith("nosidedata:"):
                checks[e] = re.search(e.split(":", 1)[1], se) is None
            elif e.startswith("meta:"):
                checks[e] = re.search(e.split(":", 1)[1], so + se) is not None
        ok = all(checks.values())
        self.record(dict(filter=filt, row=row or "(defaults)", fmt=fmt, size=f"{w}x{h}", rc=rc, checks=checks,
                         diags=diags, info=info, ok=ok, pre=pre, post=post))
        return ok

    def save(self):
        (self.out / "results.json").write_text(json.dumps(self.results, indent=1, default=str), encoding="utf-8")
        with open(self.out / "diagnostics-verbatim.txt", "w", encoding="utf-8", newline="\n") as f:
            for mid, text in sorted(self.diag_texts.items()):
                total = sum(r["diags"].get(mid, 0) for r in self.results)
                rows = sum(1 for r in self.results if mid in r["diags"])
                f.write(f"=== {mid}  (total {total} messages in {rows} runs; "
                        f"{'KNOWN-STOCK' if mid in KNOWN_STOCK else 'NOT ATTRIBUTED TO STOCK'})\n{text}\n\n")


def rows():
    """(filter, option-row, expects, kwargs) -- every documented option at a non-default value."""
    R = []
    add = lambda f, row, exp, **kw: R.append((f, row, exp, kw))
    # deband
    f = "pelorus_deband_vulkan"
    add(f, "", ["luma_changed"])
    add(f, "range=1:thry=0.25:thrc=0.25", [])
    add(f, "range=31", [])
    add(f, "grainy=0.4:grainc=0.4", ["luma_changed", "chroma_changed"])
    add(f, "softness=0", [])
    add(f, "detail=0:protect=0", [])
    add(f, "detail=0.25", [])
    for s in ("column", "row", "square_rot"):
        add(f, f"sample={s}", [])
    add(f, "blur=allrefs", [])
    add(f, "dither=none:grainy=0:grainc=0", [])
    add(f, "dither=bayer8", [])
    add(f, "dynamic=0", [])
    add(f, "meta=1", ["sidedata:User Data Unregistered SEI"])
    add(f, "planes=1", ["chroma_exact", "luma_changed"])
    add(f, "planes=2:grainc=0.2", ["luma_exact"])
    add(f, "planes=0", ["identity"])
    # analyze (pass-through)
    f = "pelorus_analyze_vulkan"
    add(f, "", ["identity", "sidedata:User Data Unregistered SEI", "meta:lavfi.pelorus.complexity="],
        post=",metadata=mode=print:file=-")
    add(f, "roi=1", ["identity"])
    add(f, "roi=1:roi_strength=1:flat=0.25:grad_lo=0:grad_hi=0.5", ["identity",
                                                                    "sidedata:Regions Of Interest"])
    add(f, "flat=0:roi=1", ["identity"])
    add(f, "grad_lo=0.5:grad_hi=0", ["identity"])
    # denoise
    f = "pelorus_denoise_vulkan"
    add(f, "", ["luma_changed"])
    add(f, "sigma=0.5:sigmac=0.5:sigmat=0.5:strength=1:strengthc=1", ["luma_changed", "chroma_changed"])
    add(f, "sigma=0:sigmac=0:sigmat=0", [])
    add(f, "blend=0", [])
    add(f, "blend=1", [])
    add(f, "tdecay=0:tcut=0", [])
    add(f, "tcut=0.5", [])
    add(f, "patch=0", [])
    add(f, "patch=3", [])
    add(f, "prev=0", [])
    add(f, "prev=4", [])
    add(f, "protect=0", [])
    add(f, "meta=1", ["sidedata:User Data Unregistered SEI"])
    add(f, "tile=1", [], same_as="")
    add(f, "patch=3:prev=1", [])
    add(f, "patch=3:prev=1:tile=1", [], same_as="patch=3:prev=1")
    add(f, "patch=0:tile=1", [], same_as="patch=0")
    add(f, "lookahead=1", [])
    add(f, "lookahead=1:prev=4:tile=1", [])
    add(f, "mc=1", [])
    add(f, "mc=1:prev=2", ["luma_changed"], pre="pelorus_mc_vulkan=meta=1,")
    add(f, "planes=1", ["chroma_exact", "luma_changed"])
    add(f, "planes=2", ["luma_exact", "chroma_changed"])
    add(f, "planes=0", ["identity"])
    # grain_estimate (pass-through)
    f = "pelorus_grain_estimate_vulkan"
    add(f, "", ["identity", "sidedata:Film grain parameters", "sidedata:User Data Unregistered SEI"])
    add(f, "model=h274", ["identity"])
    add(f, "native=0", ["identity", "nosidedata:Film grain parameters"])
    add(f, "edge=0:strength=64", ["identity"])
    add(f, "edge=1:strength=0", ["identity"])
    # mc (pass-through)
    f = "pelorus_mc_vulkan"
    add(f, "", ["identity", "sidedata:User Data Unregistered SEI"])
    add(f, "bsize=8:search=1", ["identity"])
    add(f, "bsize=32:search=256", ["identity"])
    add(f, "meta=0", ["identity", "nosidedata:User Data Unregistered SEI"])
    # dehalo
    f = "pelorus_dehalo_vulkan"
    add(f, "", ["chroma_exact"])
    add(f, "blur=1", ["chroma_exact"])
    add(f, "blur=8", ["chroma_exact"])
    add(f, "darkstr=0:brightstr=0", ["chroma_exact"])
    add(f, "lowsens=0:highsens=4", ["chroma_exact", "luma_changed"])
    add(f, "lowsens=1", ["chroma_exact"])
    add(f, "edge=0:ring=8", ["chroma_exact"])
    add(f, "edge=1:ring=1", ["chroma_exact"])
    add(f, "tile=1", ["chroma_exact"], same_as="")
    add(f, "blur=8:tile=1", ["chroma_exact"], same_as="blur=8")
    add(f, "planes=15:lowsens=0:highsens=4", ["luma_changed", "chroma_changed"])
    add(f, "planes=2:lowsens=0:highsens=4", ["luma_exact"])
    add(f, "planes=0", ["identity"])
    # aa
    f = "pelorus_aa_vulkan"
    add(f, "", ["chroma_exact", "luma_changed"])
    add(f, "blur=0", ["chroma_exact"])
    add(f, "blur=8", ["chroma_exact"])
    add(f, "depth=0", ["chroma_exact"])
    add(f, "depth=64", ["chroma_exact", "luma_changed"])
    add(f, "thresh=0", ["chroma_exact"])
    add(f, "thresh=1", ["chroma_exact"])
    add(f, "darkstr=1:edge=0", ["chroma_exact", "luma_changed"])
    add(f, "darkstr=0.5:edge=1", ["chroma_exact"])
    add(f, "fast=1", ["chroma_exact"], same_as="")
    add(f, "darkstr=0.5", ["chroma_exact"])
    add(f, "darkstr=0.5:fast=1", ["chroma_exact"], same_as="darkstr=0.5")
    add(f, "blur=8:fast=1", ["chroma_exact"], same_as="blur=8")
    add(f, "planes=15", ["luma_changed", "chroma_changed"])
    add(f, "planes=2", ["luma_exact"])
    add(f, "planes=0", ["identity"])
    # deblock
    f = "pelorus_deblock_vulkan"
    add(f, "", ["chroma_exact", "luma_changed"])
    add(f, "bsize=2", ["chroma_exact"])
    add(f, "bsize=64", ["chroma_exact"])
    add(f, "edge=0", ["chroma_exact"])
    add(f, "edge=8", ["chroma_exact"])
    add(f, "thr=0", ["chroma_exact"])
    add(f, "thr=1:str=1", ["chroma_exact", "luma_changed"])
    add(f, "str=0", ["chroma_exact"])
    add(f, "planes=15:thr=1:str=1", ["luma_changed", "chroma_changed"])
    add(f, "planes=2:thr=1:str=1", ["luma_exact", "chroma_changed"])
    add(f, "planes=0", ["identity"])
    # borderfix
    f = "pelorus_borderfix_vulkan"
    add(f, "", ["identity"])
    add(f, "left=4:right=4:top=4:bottom=4", ["luma_changed", "chroma_changed"])
    add(f, "left=4:right=4:planes=1", ["chroma_exact", "luma_changed"])
    add(f, "top=2:bottom=2:planes=14", ["luma_exact", "chroma_changed"])
    add(f, "left=4096:top=4096", [])
    add(f, "left=4:planes=0", ["identity"])
    return R


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("spec")
    ap.add_argument("--gpuav", action="store_true")
    ap.add_argument("--nosync", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--formats", default=",".join(MAIN_FORMATS))
    ap.add_argument("--extras", action="store_true", help="defaults on extra formats + odd sizes")
    ap.add_argument("--defaults-only", action="store_true")
    ap.add_argument("--row-regex", default="", help="also keep option rows matching this regex")
    a = ap.parse_args()
    sw = Sweep(a.label, a.spec, a.gpuav, sync=not a.nosync)
    only = set(x for x in a.only.split(",") if x)
    fmts = [x for x in a.formats.split(",") if x]
    all_rows = rows()
    if a.defaults_only:
        all_rows = [r for r in all_rows if r[1] == "" or (a.row_regex and re.search(a.row_regex, r[1]))]
    for fmt in fmts:
        for filt, row, exp, kw in all_rows:
            if only and filt not in only:
                continue
            sw.run_row(filt, row, exp, fmt, **kw)
    if a.extras:
        defaults = [r for r in rows() if r[1] == "" and (not only or r[0] in only)]
        for fmt in EXTRA_FORMATS:
            for filt, row, exp, kw in defaults:
                sw.run_row(filt, row, exp, fmt, **kw)
        for fmt in ("yuv420p", "nv12", "p010le"):
            for filt, row, exp, kw in defaults:
                # testsrc2 rounds 4:2:0 to even sizes; testsrc (rgb24) keeps 157x93.
                sw.run_row(filt, row, exp, fmt, w=157, h=93,
                           src="testsrc=size=157x93:rate=10,noise=alls=10:allf=t:all_seed=7", **kw)
    sw.save()
    n_fail = sum(1 for r in sw.results if not r["ok"])
    print(f"SUMMARY {a.label}: {len(sw.results)} runs, {n_fail} failing; evidence {sw.out}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
