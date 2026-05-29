#!/usr/bin/env python3
"""Switch error rate between Beagle-phased and trio-truth-phased haplotypes.

Walks every truth_phased_<child>_chr{N}.vcf.gz + child_beagle_phased_<child>_chr{N}.vcf.gz
pair under --validation-dir and aggregates switch errors per child × chromosome.

Output JSON shape:
{
  "per_child_chrom": [{child, chrom, n_het, n_switches, switch_error_rate}, ...],
  "mean_switch_error_rate": <float>,
  "n_het_total": <int>
}

Plan §6.4 phase 6d — logic unchanged from v1.1.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pysam

_RE_TRUTH = re.compile(r"^truth_phased_(?P<child>.+)_chr(?P<chrom>[^.]+)\.vcf\.gz$")


def _load_phased_hets(path: Path, sample: str) -> dict[tuple[str, int], tuple[int, int]]:
    hets: dict[tuple[str, int], tuple[int, int]] = {}
    with pysam.VariantFile(str(path)) as v:
        for rec in v.fetch():
            gt = rec.samples.get(sample, {}).get("GT")
            if gt is None or len(gt) != 2 or gt[0] == gt[1]:
                continue
            hets[(rec.chrom, rec.pos)] = tuple(gt)
    return hets


def switch_error_rate(truth_haps, inferred_haps) -> tuple[float, int]:
    n = len(truth_haps)
    if n < 2:
        return 0.0, n
    matches = [t == i for t, i in zip(truth_haps, inferred_haps)]
    switches = sum(1 for k in range(1, n) if matches[k] != matches[k - 1])
    return switches / (n - 1), n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-dir", required=True, type=Path)
    parser.add_argument("--out-report", required=True, type=Path)
    args = parser.parse_args()

    per_pair = []
    n_het_total = 0
    rate_sum = 0.0
    rate_count = 0

    for truth_path in sorted(args.validation_dir.glob("truth_phased_*.vcf.gz")):
        m = _RE_TRUTH.match(truth_path.name)
        if not m:
            continue
        child, chrom = m.group("child"), m.group("chrom")
        beagle_path = args.validation_dir / f"child_beagle_phased_{child}_chr{chrom}.vcf.gz"
        if not beagle_path.exists():
            continue

        truth_hets = _load_phased_hets(truth_path, child)
        beagle_hets = _load_phased_hets(beagle_path, child)
        common = sorted(set(truth_hets) & set(beagle_hets))
        truth_seq = [truth_hets[k] for k in common]
        beagle_seq = [beagle_hets[k] for k in common]
        rate, n = switch_error_rate(truth_seq, beagle_seq)
        per_pair.append({
            "child": child,
            "chrom": chrom,
            "n_het": n,
            "switch_error_rate": rate,
        })
        n_het_total += n
        if n >= 2:
            rate_sum += rate
            rate_count += 1

    report = {
        "per_child_chrom": per_pair,
        "mean_switch_error_rate": rate_sum / rate_count if rate_count else 0.0,
        "n_het_total": n_het_total,
    }
    args.out_report.write_text(json.dumps(report, indent=2))
    print(f"mean switch error rate: {report['mean_switch_error_rate']:.4f}")
    print("target (v1.1 baseline): <= 0.0566")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
