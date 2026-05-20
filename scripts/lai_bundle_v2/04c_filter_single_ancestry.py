#!/usr/bin/env python3
"""Filter ADMIXTURE / fastmixture output to single-ancestry training samples.

Ported from lai_bundle_build/lai_build_plan.md Phase 4c. Parametrized so the
v2 rebuild reuses the v1.1 logic verbatim.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--q-matrix", required=True, type=Path,
                        help="fastmixture .Q file (headerless, space-separated)")
    parser.add_argument("--fam", required=True, type=Path,
                        help="PLINK .fam matching the .Q row order")
    parser.add_argument("--meta", required=True, type=Path,
                        help="gnomAD HGDP+1KG metadata TSV (gnomad_meta_updated.tsv)")
    parser.add_argument("--threshold", type=float, default=0.95,
                        help="Min top-Q for single-ancestry classification (default: 0.95)")
    parser.add_argument("--out-sample-map", required=True, type=Path)
    parser.add_argument("--out-single-ancestry", required=True, type=Path)
    parser.add_argument("--out-excluded", required=True, type=Path)
    args = parser.parse_args()

    Q = np.loadtxt(args.q_matrix)
    fam = pd.read_csv(
        args.fam, sep=r"\s+", header=None,
        names=["FID", "IID", "PAT", "MAT", "SEX", "PHENO"],
    )
    if len(fam) != Q.shape[0]:
        raise SystemExit(f"row mismatch: fam={len(fam)} Q={Q.shape[0]}")

    meta = pd.read_csv(
        args.meta, sep="\t",
        usecols=["s", "hgdp_tgp_meta.Population", "hgdp_tgp_meta.Genetic.region"],
    )
    meta.columns = ["sample_id", "population", "genetic_region"]

    fam["max_q"] = Q.max(axis=1)
    fam["assigned_k"] = Q.argmax(axis=1)
    merged = fam.merge(meta, left_on="IID", right_on="sample_id", how="left")

    single = merged[merged["max_q"] >= args.threshold].copy()
    admixed = merged[merged["max_q"] < args.threshold].copy()

    print(f"Total samples: {len(merged)}")
    print(f"Single-ancestry (Q >= {args.threshold}): {len(single)}")
    print(f"Excluded admixed: {len(admixed)}")
    if not single.empty:
        print("\nPer-region (single-ancestry):")
        print(single["genetic_region"].value_counts().to_string())

    single[["IID", "genetic_region"]].to_csv(
        args.out_sample_map, sep="\t", header=False, index=False,
    )
    single.to_csv(args.out_single_ancestry, sep="\t", index=False)
    admixed[["IID", "population", "max_q"]].to_csv(
        args.out_excluded, sep="\t", index=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
