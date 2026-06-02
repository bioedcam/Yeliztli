#!/usr/bin/env python3
"""Identify validation trios from the 1000-Genomes pedigree ∩ the panel.

Outputs:
  - trio_children.txt    — one child sample ID per line
  - trio_pedigree.tsv    — child<TAB>father<TAB>mother<TAB>population<TAB>region

This is the proven v1.1 method (v1.1 `find_complete_trios.py`): the gnomAD
HGDP+1KG panel carries the 1000-Genomes parent-child relationships, but the
relationships live in the 1000G pedigree `20130606_g1k.ped`, NOT in
`gnomad_meta_updated.tsv` (which has no paternal/maternal-id columns — the
earlier meta-column approach failed loud here). So: take ped rows with both
parents present, keep trios where child + both parents are all in the panel
sample list, label each by genetic region from the gnomAD meta, and select a
few per region for a diverse validation set.

Plan §6.4 phase 6a. (Rewritten 2026-06-01 against the v1.1 reference, Step 25.)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# gnomAD meta column candidates for the per-sample genetic region / population.
_REGION_COLS = ["hgdp_tgp_meta.Genetic.region", "genetic_region"]
_POP_COLS = ["hgdp_tgp_meta.Population", "population"]
_SAMPLE_COLS = ["s", "sample_id", "sample"]


def _pick(cols, candidates, what):
    match = next((c for c in candidates if c in cols), None)
    if match is None:
        raise SystemExit(
            f"meta missing column for '{what}'; tried {candidates}; have {list(cols)[:20]}"
        )
    return match


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ped", required=True, type=Path,
                        help="1000G pedigree 20130606_g1k.ped (tab-delimited)")
    parser.add_argument("--panel-samples", required=True, type=Path,
                        help="one panel sample ID per line (bcftools query -l)")
    parser.add_argument("--meta", required=True, type=Path,
                        help="gnomAD HGDP+1KG metadata TSV (for region/population labels)")
    parser.add_argument("--per-region", type=int, default=3,
                        help="max trios to keep per genetic region (default: 3)")
    parser.add_argument("--out-trios", required=True, type=Path)
    parser.add_argument("--out-pedigree", required=True, type=Path)
    args = parser.parse_args()

    ped = pd.read_csv(args.ped, sep="\t", dtype=str)
    ped.columns = ped.columns.str.strip()
    for col in ("Individual ID", "Paternal ID", "Maternal ID"):
        if col not in ped.columns:
            raise SystemExit(f"ped missing column '{col}'; have {list(ped.columns)}")

    panel = {line.strip() for line in args.panel_samples.read_text().splitlines() if line.strip()}

    trios = ped[(ped["Paternal ID"] != "0") & (ped["Maternal ID"] != "0")].copy()
    complete = trios[
        trios["Individual ID"].isin(panel)
        & trios["Paternal ID"].isin(panel)
        & trios["Maternal ID"].isin(panel)
    ].copy()
    print(f"ped trios: {len(trios)}; complete in panel: {len(complete)}")

    meta = pd.read_csv(args.meta, sep="\t", low_memory=False)
    sample_col = _pick(meta.columns, _SAMPLE_COLS, "sample")
    region_col = _pick(meta.columns, _REGION_COLS, "region")
    pop_col = _pick(meta.columns, _POP_COLS, "population")
    labels = meta[[sample_col, region_col, pop_col]].rename(
        columns={sample_col: "Individual ID", region_col: "region", pop_col: "population"}
    )
    complete = complete.merge(labels, on="Individual ID", how="left")
    complete["region"] = complete["region"].fillna("UNKNOWN")

    selected = (
        complete.sort_values("Individual ID").groupby("region", sort=True).head(args.per_region)
    )
    selected = selected.drop_duplicates(subset=["Individual ID"])

    cols = ["Individual ID", "Paternal ID", "Maternal ID", "population", "region"]
    pedigree = selected[cols].rename(
        columns={"Individual ID": "child", "Paternal ID": "father", "Maternal ID": "mother"}
    )
    pedigree.to_csv(args.out_pedigree, sep="\t", index=False)
    pedigree[["child"]].to_csv(args.out_trios, sep="\t", index=False, header=False)

    print(f"selected {len(pedigree)} trios across {pedigree['region'].nunique()} region(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
