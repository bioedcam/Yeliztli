#!/usr/bin/env python3
"""One-time script to augment the PCA bundle NPZ with 23andMe rsID lookup.

The PCA bundle's AIM identifiers use chr:pos:ref:alt format (GRCh38 coordinates),
but 23andMe raw data uses rsIDs. This script reads the LAI bundle's liftover TSV
(rsID → GRCh38 position) and adds an `aim_rsids_23andme` array to the NPZ so
genotype matching can use rsIDs directly.

Usage:
    python scripts/augment_pca_bundle_rsids.py \
        --npz backend/data/panels/ancestry_pca_bundle.npz \
        --liftover /path/to/lai_bundle/liftover/rsid_to_grch38.tsv

This was run once during v1.1 bundle preparation. The augmented NPZ is committed
to the repo — this script is kept for reproducibility.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npz", type=Path, required=True, help="Path to ancestry_pca_bundle.npz")
    parser.add_argument("--liftover", type=Path, required=True, help="Path to rsid_to_grch38.tsv")
    args = parser.parse_args()

    pca = np.load(args.npz, allow_pickle=True)

    # Build (chrom, pos) → rsID lookup from liftover TSV
    pos_to_rsid: dict[tuple[str, int], str] = {}
    with open(args.liftover) as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) != 3:
                continue
            rsid, chrom_str, pos = parts[0], parts[1].replace("chr", ""), int(parts[2])
            pos_to_rsid[(chrom_str, pos)] = rsid

    print(f"Loaded {len(pos_to_rsid)} liftover entries")

    # Match each AIM to an rsID
    aim_chroms = pca["aim_chroms"]
    aim_positions = pca["aim_positions_grch38"]
    aim_rsids_23andme = []
    matched = 0

    for i in range(len(aim_chroms)):
        rsid = pos_to_rsid.get((str(aim_chroms[i]), int(aim_positions[i])))
        aim_rsids_23andme.append(rsid or "")
        if rsid:
            matched += 1

    print(f"Matched: {matched}/{len(aim_chroms)} ({100 * matched / len(aim_chroms):.1f}%)")

    # Save augmented NPZ
    arrays = {k: pca[k] for k in pca.files}
    arrays["aim_rsids_23andme"] = np.array(aim_rsids_23andme, dtype="<U16")
    np.savez_compressed(args.npz, **arrays)
    print(f"Saved augmented NPZ to {args.npz}")


if __name__ == "__main__":
    main()
