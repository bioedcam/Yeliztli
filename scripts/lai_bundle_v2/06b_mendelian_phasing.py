#!/usr/bin/env python3
"""Truth-phase trio children via Mendelian inheritance.

For each het site in the child, resolve phase from parental genotypes.
- Father hom-ref, mother carries alt → alt allele inherited from mother.
- Mother hom-ref, father carries alt → alt allele inherited from father.
- Both parents het → ambiguous, skip.

Outputs per child: truth_phased_<child>_chr{N}.vcf.gz inside --in-dir.

Plan §6.4 phase 6b — logic unchanged from v1.1.
"""
from __future__ import annotations

import argparse
import shlex
from pathlib import Path

import pandas as pd
import pysam


def resolve_phase(child_gt, father_gt, mother_gt):
    """Return (hap1, hap2) or None when ambiguous."""
    if child_gt != (0, 1) and child_gt != (1, 0):
        return None
    if father_gt == (0, 0) and 1 in mother_gt:
        return (0, 1)
    if mother_gt == (0, 0) and 1 in father_gt:
        return (1, 0)
    if father_gt in [(0, 1), (1, 0)] and mother_gt in [(0, 1), (1, 0)]:
        return None
    if father_gt == (1, 1) and mother_gt == (0, 0):
        return (1, 0)
    if father_gt == (0, 0) and mother_gt == (1, 1):
        return (0, 1)
    return None


def phase_child(panel_vcf: Path, child: str, father: str, mother: str, out_vcf: Path) -> int:
    sites_phased = 0
    with pysam.VariantFile(str(panel_vcf)) as vin:
        header = vin.header.copy()
        header.add_meta("phased_by", value="mendelian_inheritance")
        with pysam.VariantFile(str(out_vcf), "wz", header=header) as vout:
            for rec in vin.fetch():
                child_gt = rec.samples.get(child, {}).get("GT")
                father_gt = rec.samples.get(father, {}).get("GT")
                mother_gt = rec.samples.get(mother, {}).get("GT")
                if child_gt is None or father_gt is None or mother_gt is None:
                    continue
                phased = resolve_phase(tuple(child_gt), tuple(father_gt), tuple(mother_gt))
                if phased is None:
                    continue
                new_rec = rec.copy()
                # Strip everyone but child, write phased genotype.
                for s in list(new_rec.samples):
                    if s != child:
                        del new_rec.samples[s]
                new_rec.samples[child]["GT"] = phased
                new_rec.samples[child].phased = True
                vout.write(new_rec)
                sites_phased += 1
    pysam.tabix_index(str(out_vcf), preset="vcf", force=True)
    return sites_phased


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pedigree", required=True, type=Path)
    parser.add_argument("--in-dir", required=True, type=Path,
                        help="validation working directory containing trio_truth_chr{N}.vcf.gz")
    parser.add_argument("--chroms", required=True, type=str,
                        help="space-separated list, e.g. '1 2 3 ... 22'")
    args = parser.parse_args()

    pedigree = pd.read_csv(args.pedigree, sep="\t")
    chroms = shlex.split(args.chroms)
    for _, row in pedigree.iterrows():
        child, father, mother = row["child"], row["father"], row["mother"]
        for chrom in chroms:
            panel = args.in_dir / f"trio_truth_chr{chrom}.vcf.gz"
            out = args.in_dir / f"truth_phased_{child}_chr{chrom}.vcf.gz"
            if out.exists():
                continue
            n = phase_child(panel, child, father, mother, out)
            print(f"{child} chr{chrom}: {n} truth-phased het sites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
