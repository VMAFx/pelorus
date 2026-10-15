<!-- markdownlint-disable MD013 MD060 -->
# Research 0174 E3: AV1 encoder-QP normalization table

Checked on 2026-10-15. This closes E3 from
[research 0174](0174-abi-1-4-telemetry-provenance.md): the 0..63 AV1 encoder
QP scale now has a pinned luma AC step table for 8-, 10- and 12-bit streams.

## Result

Pelorus stores 192 values: 64 encoder-QP entries at each of three coded bit
depths. Every value was regenerated from libaom v3.15.2 and compared with
`av1_encoder_ac_qtx` in `libpelorus/src/telemetry.c`: 192 compared, 0
mismatches. The comma-separated sampled values have SHA-256
`404b29899dac4fe6d3fdfa91489d3b25b75dbee0bd849b9d24782954195a799a`.

For each coded bit depth and encoder QP `q`, regeneration does this:

1. Read `qindex = quantizer_to_qindex[q]` from libaom.
2. Read luma `ac_q` at that qindex from `ac_qlookup_QTX`,
   `ac_qlookup_10_QTX` or `ac_qlookup_12_QTX`.
3. Store `ac_q` in the corresponding Pelorus table row.

Normalization removes transform and sample-depth scaling with
`qstep_8bit = ac_q / (8 << (bd-8))`, then computes
`6 * log2(qstep_8bit / 0.625)`. Luma AC is the selected table because the
telemetry contract defines the normalized value from the frame's luma AC
step. Fractional encoder QP interpolates `log2(qstep_8bit)` between adjacent
integer entries; equivalently, it linearly interpolates the two normalized-QP
endpoints.

## Pinned sources

The checked libaom tag `v3.15.2` peels to commit
`af3dc9aadc793c1b00edc003e3fc19a44fdeb574`.

| Input | Source | SHA-256 of checked file |
|---|---|---|
| Luma AC QTX tables for 8, 10 and 12 bits | [`av1/common/quant_common.c`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/av1/common/quant_common.c) | `dd419b0adfd6178da6b9df1af1b1592feb65ac22811bc7954624fb6caa624a66` |
| Encoder QP 0..63 to qindex mapping | [`av1/encoder/av1_quantize.c`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/av1/encoder/av1_quantize.c) | `e2662e944f78a34844bbe8fc12ffc66fd05be2942b9670312ca9a348778ca348` |

The tag and commit were checked with:

```console
git ls-remote --tags https://aomedia.googlesource.com/aom refs/tags/v3.15.2 refs/tags/v3.15.2^{}
```

The peeled line was:

```text
af3dc9aadc793c1b00edc003e3fc19a44fdeb574 refs/tags/v3.15.2^{}
```

The comparison extracted the 64 integers from `quantizer_to_qindex`, indexed
each of the three 256-entry AC tables, flattened the results in 8/10/12-bit
row order, and compared them integer-for-integer with the three rows in
`av1_encoder_ac_qtx`. Runtime anchor tests cover QP 0 and 63 at 8 bits, QP 34
at 10 and 12 bits, and fractional QP 34.5 in
`libpelorus/test/telemetry_adapter_test.c`.

From the Pelorus repository root, with libaom v3.15.2 checked out at the
commit above as `<libaom-checkout>`, replay the extraction and comparison with:

```console
python3 -I - <libaom-checkout> <<'PY'
import hashlib
import re
import sys
from pathlib import Path


def values(path, name):
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\b" + re.escape(name) + r"[^=]*=\s*\{(.*?)\n\};", text, re.S)
    if match is None:
        raise SystemExit(f"missing array: {name}")
    return [int(value) for value in re.findall(r"\b\d+\b", match.group(1))]


root = Path(sys.argv[1])
quantizers = values(root / "av1/encoder/av1_quantize.c", "quantizer_to_qindex")
pelorus = values(Path("libpelorus/src/telemetry.c"), "av1_encoder_ac_qtx")
sampled = []
for name in ("ac_qlookup_QTX", "ac_qlookup_10_QTX", "ac_qlookup_12_QTX"):
    table = values(root / "av1/common/quant_common.c", name)
    sampled.extend(table[qindex] for qindex in quantizers)
mismatches = sum(actual != expected for actual, expected in zip(pelorus, sampled))
digest = hashlib.sha256(",".join(map(str, sampled)).encode()).hexdigest()
print(f"pelorus_values={len(pelorus)} sampled_values={len(sampled)} mismatches={mismatches}")
print(f"sampled_sha256={digest}")
raise SystemExit(mismatches != 0 or len(pelorus) != 192 or len(sampled) != 192)
PY
```

The checked run printed:

```text
pelorus_values=192 sampled_values=192 mismatches=0
sampled_sha256=404b29899dac4fe6d3fdfa91489d3b25b75dbee0bd849b9d24782954195a799a
```
