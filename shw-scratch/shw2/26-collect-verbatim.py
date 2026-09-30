#!/usr/bin/env python3
"""shw-2 step 26: every distinct validation-layer message seen in any shw-2 run,
verbatim (first occurrence; 64-bit handles masked), with per-stage counts."""
import pathlib, re, collections
ROOT = pathlib.Path(r"C:/tmp/pel/shw-results/shw-2")
HDR = re.compile(r"^Validation (Error|Warning|Information|Performance Warning): \[ ([^\]]+) \]")
first = {}
where = collections.defaultdict(collections.Counter)
for f in sorted(ROOT.rglob("*")):
    if not f.is_file() or f.suffix not in (".stdout", ".stderr", ".txt", ".log"):
        continue
    if f.name == "validation-messages-verbatim.txt":
        continue
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        continue
    i = 0
    stage = f.relative_to(ROOT).parts[0]
    while i < len(lines):
        m = HDR.match(lines[i])
        if not m:
            i += 1
            continue
        block = [lines[i]]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].startswith("Validation "):
            block.append(lines[i]); i += 1
        mid = m.group(2).strip()
        where[mid][stage] += 1
        first.setdefault(mid, (str(f.relative_to(ROOT)), re.sub(r"0x[0-9a-fA-F]{6,}", "0x…", "\n".join(block))))
out = ROOT / "validation-messages-verbatim.txt"
with open(out, "w", encoding="utf-8", newline="\n") as o:
    o.write("Every distinct Khronos validation message in shw-2 evidence (first occurrence, handles masked).\n")
    o.write("Counts are per top-level stage directory/log (a message may be counted in both a .log and its per-run file).\n\n")
    for mid in sorted(first):
        src, text = first[mid]
        o.write(f"=== {mid}\nfirst seen: {src}\ncounts: {dict(where[mid])}\n{text}\n\n")
print(out, len(first), "distinct ids")
for mid in sorted(first):
    print(" ", mid, sum(where[mid].values()))
