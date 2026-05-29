#!/usr/bin/env python3
"""LAI accuracy on held-out single-ancestry samples.

For every Gnomix-predicted window of a held-out sample, score whether the
predicted population matches the sample's true (Phase 4) population label.

Expected upstream:
  - Beagle-phased held-out sample VCFs (chrN) under --validation-dir
  - Gnomix inference results placed as lai_<sample>_chr{N}.tsv in --validation-dir
    (Gnomix runtime invocation lives in backend/analysis/gnomix_inference.py;
    for cluster validation it's typically driven by 06c-style scripts that
    reuse the Beagle output.)

Output JSON shape:
{
  "per_sample": [{sample, true_pop, n_windows, correct, accuracy}, ...],
  "overall_accuracy": <float>,
  "per_population": {pop: {n_windows: int, correct: int, accuracy: float}}
}

Plan §6.4 phase 6e — logic unchanged from v1.1.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gnomix-dir", required=True, type=Path)
    parser.add_argument("--validation-dir", required=True, type=Path)
    parser.add_argument("--single-ancestry", required=True, type=Path,
                        help="single_ancestry_samples.tsv from Phase 4")
    parser.add_argument("--out-report", required=True, type=Path)
    args = parser.parse_args()

    single = pd.read_csv(args.single_ancestry, sep="\t")
    truth = dict(zip(single["IID"].astype(str), single["genetic_region"]))

    per_sample = []
    pop_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"n_windows": 0, "correct": 0})
    total_windows = 0
    total_correct = 0

    for tsv in sorted(args.validation_dir.glob("lai_*_chr*.tsv")):
        # lai_<sample>_chr<N>.tsv — Gnomix output, one row per window.
        stem = tsv.stem  # lai_<sample>_chr<N>
        sample = stem.split("_chr")[0].removeprefix("lai_")
        true_pop = truth.get(sample)
        if true_pop is None:
            continue
        df = pd.read_csv(tsv, sep="\t")
        if df.empty:
            continue
        windows = len(df) * 2  # diploid
        hap1_correct = (df.get("hap1_label") == true_pop).sum()
        hap2_correct = (df.get("hap2_label") == true_pop).sum()
        correct = int(hap1_correct + hap2_correct)
        per_sample.append({
            "sample": sample,
            "true_pop": true_pop,
            "n_windows": windows,
            "correct": correct,
            "accuracy": correct / windows if windows else 0.0,
        })
        pop_stats[true_pop]["n_windows"] += windows
        pop_stats[true_pop]["correct"] += correct
        total_windows += windows
        total_correct += correct

    per_population = {
        pop: {
            "n_windows": v["n_windows"],
            "correct": v["correct"],
            "accuracy": v["correct"] / v["n_windows"] if v["n_windows"] else 0.0,
        }
        for pop, v in pop_stats.items()
    }
    report = {
        "per_sample": per_sample,
        "overall_accuracy": total_correct / total_windows if total_windows else 0.0,
        "per_population": per_population,
    }
    args.out_report.write_text(json.dumps(report, indent=2))
    print(f"overall LAI accuracy: {report['overall_accuracy']:.4f}")
    print("target (v1.1 baseline): >= 0.88 mean per-window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
