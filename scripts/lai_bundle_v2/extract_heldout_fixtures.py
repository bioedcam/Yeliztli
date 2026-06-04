#!/usr/bin/env python3
"""Extract AncestryDNA-density (GRCh38) fixtures for the held-out validation set.

For each held-out 1000G/HGDP sample, write its true genotypes at the SAME 666k
site set as the HG01502 diagnostic fixture (06_validation/heldout_sites.tsv),
pulled from the phasing panel (03_subsetted_panels/ref_panel_chrN.vcf.gz). The
sample is held OUT of gnomix training (sample_map.txt) but is in the panel, so
this is a genuine held-out inference test. Output mirrors the HG01502 fixture
format (rsid, chromosome, GRCh38 position, allele1, allele2).
"""

from __future__ import annotations

import gzip
import subprocess
import sys
from pathlib import Path

base = Path.home() / "lai_bundle_v2"
panel_dir = base / "03_subsetted_panels"
val = base / "06_validation"
outdir = val / "heldout_fixtures"
outdir.mkdir(exist_ok=True)

# (chrom_noprefix, pos) -> rsid
sites: dict[tuple[str, str], str] = {}
for line in (val / "heldout_sites.tsv").read_text().splitlines():
    p = line.split("\t")
    if len(p) >= 3:
        sites[(p[1], p[2])] = p[0]
print(f"site map: {len(sites):,} sites", flush=True)

held = [
    ln.split("\t")
    for ln in (val / "held_out_validation.tsv").read_text().splitlines()[1:]
    if ln.strip()
]
iids = [h[0] for h in held]
region = {h[0]: h[1] for h in held}
acc: dict[str, list[str]] = {i: [] for i in iids}

for n in range(1, 23):
    panel = panel_dir / f"ref_panel_chr{n}.vcf.gz"
    cmd = [
        "bcftools",
        "query",
        "-s",
        ",".join(iids),
        "-f",
        r"%CHROM\t%POS\t%REF\t%ALT[\t%TGT]\n",
        str(panel),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"chr{n} bcftools ERROR: {proc.stderr[:300]}", flush=True)
        sys.exit(1)
    kept = 0
    for row in proc.stdout.splitlines():
        f = row.split("\t")
        if len(f) < 4 + len(iids):
            continue
        chrom = f[0].replace("chr", "")
        pos = f[1]
        rsid = sites.get((chrom, pos))
        if rsid is None:
            continue
        kept += 1
        for iid, tgt in zip(iids, f[4:]):
            al = tgt.replace("|", "/").split("/")
            if len(al) != 2 or "." in al or "" in al:
                continue
            acc[iid].append(f"{rsid}\t{chrom}\t{pos}\t{al[0]}\t{al[1]}")
    print(f"chr{n}: kept {kept} sites", flush=True)

for iid in iids:
    out = outdir / f"{iid}_{region[iid]}.adna.txt.gz"
    with gzip.open(out, "wt") as fh:
        fh.write(f"#held-out 1000G/HGDP sample {iid} ({region[iid]}); NOT in gnomix training\n")
        fh.write("#Derived from public 1000G+HGDP phased reference; NOT real user data.\n")
        fh.write("rsid\tchromosome\tposition\tallele1\tallele2\n")
        fh.write("\n".join(acc[iid]) + "\n")
    print(f"WROTE {out.name}: {len(acc[iid]):,} variants", flush=True)
print("DONE_EXTRACT", flush=True)
