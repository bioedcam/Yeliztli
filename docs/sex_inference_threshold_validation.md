# Sex-Inference Threshold Validation — Bio-Validator Attestation

**Plan reference:** [§9.4 Biological sex inference](AncestryDNA_Integration_Plan.md#94-biological-sex-inference)
**Implementation step:** Step 53 (IND-08 part c) — blocks Step 54
**Attestation date:** 2026-05-21
**Attested by:** bio-validator subagent

This document attests that the Plan §9.4 sex-inference thresholds were validated
against a real raw export and against the three committed synthetic fixtures
prior to wiring them into `backend/services/sex_inference.py` at Step 54.

Per Plan §9.4 step 3 and CLAUDE.md SOP §1 (PRD §11/§12 privacy posture), the
report below carries **aggregate counts and rates only** — no genotype rows,
rsIDs, or coordinates are reproduced from the real export.

---

## 1. Validated thresholds

| Constant | Validated value | Source |
|----------|-----------------|--------|
| `_THRESHOLD_XY_CONFIRM` | **0.30** | Plan §9.4 literature default |
| `_THRESHOLD_PAR_NOISE`  | **0.10** | Plan §9.4 literature default |
| `_PAR1` (GRCh37) | `(60001, 2_699_520)` | Plan §9.4 |
| `_PAR2` (GRCh37) | `(154_931_044, 155_260_560)` | Plan §9.4 |

**Outcome.** No tuning was required. The literature-default thresholds
classify the local real AncestryDNA V2.0 export *and* all three committed
synthetic fixtures correctly. Step 54 must ship `sex_inference.py` with
exactly these two threshold values.

---

## 2. Real-export validation

### 2.1 Source

- **Vendor / version / build:** AncestryDNA V2.0 / GRCh37
- **File location:** local-only (`AncestryDNA.txt`, gitignored per `.gitignore:77`)
- **Total variants parsed:** 677,436
- **Ground truth (known to user):** XX

The export is never committed; this attestation is the only artifact that
crosses the repo boundary, and it carries aggregate counts only.

### 2.2 Aggregate counts (chrX)

| Bucket | Count |
|---|---|
| chrX calls (total) | 25,278 |
| PAR (pre-filtered, GRCh37 PAR1+PAR2) | 32 |
| non-PAR typed (het+hom) | 25,237 |
| &nbsp;&nbsp;non-PAR heterozygous | 5,998 |
| &nbsp;&nbsp;non-PAR homozygous | 19,239 |
| non-PAR no-call | 9 |
| **non-PAR het rate** | **0.238** |

### 2.3 Aggregate counts (chrY)

| Bucket | Count |
|---|---|
| chrY calls (total) | 1,665 |
| chrY non-no-call | 3 |
| **chrY non-no-call rate** | **0.002** |

### 2.4 Classification under default thresholds

Algorithm walk per Plan §9.4 (order is load-bearing):

1. **Non-PAR chrX heterozygous count = 5,998 ≥ 1** → **dispositive XX**.
   Males cannot be heterozygous on a non-PAR chrX locus; the algorithm
   short-circuits here and never reads the chrY rate.
2. The chrY non-no-call rate (0.002) is well below `_THRESHOLD_PAR_NOISE`
   (0.10), confirming there is no chrY signal that could have driven a
   false XY confirmation if the dispositive branch had not fired.

**Classification: `XX`** — matches ground truth.

### 2.5 Reproduction command

```bash
python scripts/validate_sex_thresholds.py <path-to-export> --json
```

Re-running the script against the local real export at the validated
thresholds (`--xy-threshold 0.30 --par-noise 0.10`, which are the defaults)
must reproduce the counts in §2.2–§2.3 and the classification in §2.4 byte-
for-byte. If a future export update yields different counts, repeat this
attestation before merging any change to either threshold or the Plan §9.4
algorithm.

---

## 3. Synthetic-fixture corroboration

The three fixtures under `tests/fixtures/sex_inference_synthetic/` (Step 52)
hand-fabricate AncestryDNA V2.0-format rows that exercise each Plan §9.4
branch. CI runs `tests/backend/test_validate_sex_thresholds.py` against
them; the same script + thresholds produce these results today:

| Fixture | non-PAR het | non-PAR hom | chrY rate | Classification | Expected |
|---|---:|---:|---:|---|---|
| `xx_sample.txt` | 2 | 2 | 0.000 | `XX` | `XX` |
| `xy_sample.txt` | 0 | 4 | 0.800 | `XY` | `XY` |
| `manual_review_sample.txt` | 0 | 4 | 0.200 | `manual_review` | `manual_review` |

The XY fixture's chrY rate (0.80) is well above `_THRESHOLD_XY_CONFIRM`
(0.30); the `manual_review` fixture's chrY rate (0.20) sits inside the
`(0.10, 0.30]` band by construction; the XX fixture's heterozygous chrX
call is dispositive regardless of chrY. Each fixture exercises exactly one
classification branch, so any future threshold revision will surface as a
CI failure at `test_validate_sex_thresholds.py` before reaching this doc.

---

## 4. Risk-register check

- **R-04 Annotation pipeline regression** — not engaged: this is a service
  parameter, not a pipeline change. Step 54 will assert byte-identical
  haplogroup output on existing 23andMe XX + XY regression fixtures.
- **Privacy (PRD §11–§12)** — satisfied: only aggregate counts and rates
  appear here; no rsIDs, coordinates, or genotype rows from the real export
  cross the repo boundary. The real export remains gitignored.

---

## 5. Sign-off

> The Plan §9.4 sex-inference algorithm — running with `_THRESHOLD_XY_CONFIRM = 0.30`
> and `_THRESHOLD_PAR_NOISE = 0.10` — correctly classifies (a) the local real
> AncestryDNA V2.0 export (known XX) as `XX` via the dispositive non-PAR-het
> branch and (b) all three committed synthetic fixtures
> (`xx_sample.txt`, `xy_sample.txt`, `manual_review_sample.txt`) into their
> declared branches. No tuning is required. Step 54 is unblocked.
>
> — bio-validator subagent, 2026-05-21
