#!/usr/bin/env python3
"""Extract single-ancestry trio children from gnomAD HGDP+1KG metadata.

Outputs:
  - trio_children.txt    — one child sample ID per line
  - trio_pedigree.tsv    — child<TAB>father<TAB>mother<TAB>population

Both parents must be present in the reference panel; the child must pass the
single-ancestry filter from Phase 4. Plan §6.4 phase 6a (unchanged from v1.1).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _resolve_columns(meta: pd.DataFrame) -> tuple[str, str, str, str]:
    candidates = {
        "sample": ["s", "sample_id", "sample"],
        "pat": ["hgdp_tgp_meta.paternal_id", "paternal_id", "father_id"],
        "mat": ["hgdp_tgp_meta.maternal_id", "maternal_id", "mother_id"],
        "pop": ["hgdp_tgp_meta.Population", "population"],
    }
    resolved = {}
    for k, names in candidates.items():
        match = next((c for c in names if c in meta.columns), None)
        if match is None:
            raise SystemExit(
                f"metadata missing column for '{k}'; tried {names}; have {list(meta.columns)[:20]}"
            )
        resolved[k] = match
    return resolved["sample"], resolved["pat"], resolved["mat"], resolved["pop"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meta", required=True, type=Path)
    parser.add_argument("--single-ancestry", required=True, type=Path,
                        help="single_ancestry_samples.tsv from Phase 4")
    parser.add_argument("--out-trios", required=True, type=Path)
    parser.add_argument("--out-pedigree", required=True, type=Path)
    args = parser.parse_args()

    meta = pd.read_csv(args.meta, sep="\t")
    single = pd.read_csv(args.single_ancestry, sep="\t")
    sample_col, pat_col, mat_col, pop_col = _resolve_columns(meta)

    sample_ids = set(meta[sample_col].astype(str))
    single_ids = set(single["IID"].astype(str))

    trios = []
    for _, row in meta.iterrows():
        child = str(row[sample_col])
        pat = str(row[pat_col]) if pd.notna(row[pat_col]) else ""
        mat = str(row[mat_col]) if pd.notna(row[mat_col]) else ""
        if not pat or not mat:
            continue
        if pat not in sample_ids or mat not in sample_ids:
            continue
        if child not in single_ids:
            continue
        trios.append((child, pat, mat, row.get(pop_col, "")))

    pedigree = pd.DataFrame(trios, columns=["child", "father", "mother", "population"])
    pedigree = pedigree.drop_duplicates(subset=["child"])
    pedigree.to_csv(args.out_pedigree, sep="\t", index=False)
    pedigree[["child"]].to_csv(args.out_trios, sep="\t", index=False, header=False)

    print(f"identified {len(pedigree)} single-ancestry trio children")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
