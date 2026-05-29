# Multi-Source Sample Merging

GenomeInsight lets you upload more than one raw data file for the same person — for example, a 23andMe export and an AncestryDNA export — and combine them into a single richer sample. This guide walks through the full end-user flow: from two separate uploads to a merged dashboard with a concordance report and post-merge VUS re-watch.

For background on the underlying data model, see the [AncestryDNA Integration Plan §10](AncestryDNA_Integration_Plan.md#10-sample-merging).

---

## When to merge

Merging is appropriate when:

- Both raw files come from the **same biological individual**.
- You want a single dashboard reflecting the **union** of variants across both files (~840k SNPs from 23andMe v5 + AncestryDNA v2.0, vs. ~640k or ~700k from either alone).
- You want explicit visibility into **where the two files agree, where they disagree, and where each one fills in a gap** the other left.

Merging is *not* the same thing as family analysis. It combines multiple samples from one person, not samples from multiple people. Family / trio analysis is post-v1.

---

## Step 1 — Upload both raw files

Each file is uploaded independently using the standard [upload flow](usage-guide.md#uploading-data). At the end of the second upload you have two `samples` rows, each with its own annotation run, dashboard, and per-sample database. No merging has happened yet.

Supported source pairs in v1:

| File A | File B |
|--------|--------|
| 23andMe v3 / v4 / v5 | AncestryDNA v2.0 |
| 23andMe (any supported version) | 23andMe (any supported version) |
| AncestryDNA v2.0 | AncestryDNA v2.0 |

If your installed VEP bundle is older than `v2.0.0`, an AncestryDNA upload is **gated** with a one-click "Update VEP bundle (~600 MB)" banner — the bundle must include the AncestryDNA rsID catalog before annotation can produce reliable findings. Apply the update; the upload retries automatically.

---

## Step 2 — Create an individual and link both samples

Samples become mergeable only after both are linked to the same **individual**. Individuals are introduced in [Phase 2 of the AncestryDNA integration](AncestryDNA_Integration_Plan.md#9-individuals-model); they are an explicit user-managed grouping, never auto-detected.

1. Open **Settings → Samples**.
2. On the first sample row, click **Assign to individual** and choose **Create new…**. Give the individual a display name (e.g., your initials or "Self"). Optionally record biological sex and free-form notes.
3. On the second sample row, click **Assign to individual** and choose the individual you just created.
4. Open the **sample selector** in the top navigation. Both samples now appear nested under the individual's display name. Samples not yet linked appear under the **Unassigned** group.

You can unlink at any time from the same dropdown. Unlinking does not delete the sample or its data — it only removes the grouping.

---

## Step 3 — Open the individual's detail page

Click the individual's name in the sample selector to open `/individuals/{id}`. The page shows:

- Individual metadata (display name, notes, biological sex, dates).
- A table of linked samples with annotation status, file format, and variant counts.
- **Aggregated high-confidence findings** — the union of 3- and 4-star findings across linked samples, deduplicated by rsID and tagged with which sample each finding came from. This view exists *before* you merge; merging is a separate, opt-in operation.

When the individual has two or more samples linked and no existing merged sample, a **Merge samples** button appears.

---

## Step 4 — Run the merge wizard

Click **Merge samples**. A modal opens with three steps.

### 4.1 — Pick a strategy

A discordant locus is a coordinate where both files made a call but the calls disagree. The merge strategy decides what genotype lands on the merged sample at those loci.

| Strategy | Behavior at a discordant locus | Genotype written |
|----------|--------------------------------|------------------|
| **`flag_only`** *(default)* | Withholds the call. The losing call is *not* discarded — both source genotypes are preserved in the row's `discordant_alt_genotype` column for forensics. | `??` (canonical "ambiguous" sentinel — analysis modules treat it as a no-call, the same as `--`) |
| `prefer_23andme` | Keeps the 23andMe call. The AncestryDNA call is preserved in `discordant_alt_genotype`. | The 23andMe genotype |
| `prefer_ancestrydna` | Symmetric. Keeps the AncestryDNA call; the 23andMe call is preserved in `discordant_alt_genotype`. | The AncestryDNA genotype |

`flag_only` is the clinically safest default — it deliberately withholds a call rather than picking one, so downstream analyses won't surface a finding driven by an unresolved conflict. Pick a `prefer_*` strategy only when you have a reason to trust one platform over the other for a specific run (e.g., a sample with degraded chip quality on one side).

### 4.2 — Preview concordance

The wizard runs a dry-run pass (≈2–5 seconds) and shows a per-bucket count:

- **Match** — both files called the same genotype.
- **Filled no-call** — one file made a call, the other was a no-call. The merged sample keeps the called genotype, lifting coverage at this locus.
- **Discordant** — both files called, but the calls disagree. Strategy determines what lands.
- **Unique to S₁ / S₂** — only one file covers this locus. The merged sample gets it.
- **Collapsed rsID** *(small count)* — different rsIDs at the same physical coordinate, merged to one row. The discarded rsID is preserved in the `alt_rsid` column.

Together these buckets cover every locus exactly once; their sum is the merged sample's variant count.

### 4.3 — Confirm

Clicking **Merge** kicks off the merge job. The wizard shows live progress via SSE: write phase, then the standard annotation phase against the new merged sample. On completion you are redirected to the merged sample's dashboard.

The merged sample is an independent row in `samples` with `file_format = "merged_v1"` and a deterministic `file_hash` derived from the source samples' hashes plus the strategy. Merging `[S1, S2]` and `[S2, S1]` (different request order) produce different hashes, because order affects the rsID-tiebreaker at collapsed-rsID loci.

---

## Step 5 — Use the merged dashboard

The merged sample's dashboard works exactly like any other sample's: every analysis module reads from its per-sample DB without any special branches. The differences are surface-level:

- The **sample selector** shows the merged sample nested under the individual, alongside its two sources.
- The **variant table** for the merged sample gains two new filterable columns:
  - **Source** — `S₁` / `S₂` / `both`. Lets you focus on what each platform contributed.
  - **Concordance** — `match` / `filled_nocall` / `discordant` / `unique`. Lets you, for example, hide every discordant locus while reviewing high-confidence findings.
- Findings derived from a discordant locus on `flag_only` will not appear, because the genotype is `??` (a no-call) until you resolve it.

---

## Step 6 — Read the concordance report

Open **Concordance report** from the merged dashboard (also accessible at `/samples/{merged_id}/concordance`). The report has two parts:

1. **Summary card** — the same bucket counts you saw in the merge wizard's preview, plus the merge strategy and the source-sample IDs and `file_hash`es. This is the audit-trail view: anyone reading the report can resolve which two source files produced this merged sample, with what strategy, and on what date.
2. **Discordant loci table** — paginated table of every locus where both files made disagreeing calls. Columns include `chrom`, `pos`, `rsid`, gene (joined from `annotated_variants`), the S₁ genotype, the S₂ genotype, and which call (if any) won under the active strategy. Ordered by `(chrom, pos)`. Defaults to 50 rows per page, capped at 500.

If a locus interests you — for example, a discordant call inside a clinically actionable gene — click through to the variant detail page from the row to review consequence and population frequency before deciding whether to act on it.

---

## Step 7 — Post-merge VUS re-watch

If you had **watched** variants on the source samples (see the [VUS Watching](usage-guide.md#vus-watching) section), some of those watches may not carry over automatically. The merged sample is an independent database; tags and watches are *not* propagated across the merge. Two specific cases lose visibility:

- A **discarded rsID at a collapsed-rsID locus** — only one rsID survives on the merged sample, so a watch on the discarded rsID has no row to attach to.
- A **private rsID** that only one source file carried but that doesn't appear on the merged sample for any reason.

Once the merged sample's annotation finishes, a **Post-merge re-watch** modal automatically appears on the dashboard. It lists every source-sample watch that does not transfer cleanly, paired (when possible) with the merged sample's chosen rsID and original watch notes. Click **Re-watch** per row to migrate the watch to the merged sample, or **Re-watch all** to batch the migration. The modal is dismissible — you can also reopen it later, watches on the source samples remain intact regardless.

---

## Source-deletion cascade

If you later delete a source sample that participated in a merge, GenomeInsight will surface a single confirmation showing every merged sample that depends on it. Confirming the delete removes the merged samples first, then the source. The other source sample is untouched. This guarantees there is no merged sample on disk whose provenance points at a missing source.

If you do not want to delete the merged samples, abort the confirmation and unlink (rather than delete) the source.

---

## Re-merging

You can re-merge the same two sources later — for example, with a different strategy, or after a bundle upgrade. The new merged sample has a distinct `file_hash` (because the strategy or the schema version contributes to the hash) and exists alongside the prior merged sample until you choose to delete one.

---

## Limitations

- Merging is **always two samples** in v1. Three- or four-way merges (e.g., 23andMe + AncestryDNA + MyHeritage) are post-v1.
- Tags and watches **do not propagate** across the merge. The re-watch modal addresses watches; tags must be reapplied manually if needed.
- Linking is **explicit** — GenomeInsight never auto-merges samples it thinks belong to the same person. A backfill script (`scripts/backfill_individuals.py`) can suggest candidate pairs by `file_hash` or near-matching name+date for review, but never executes the link.
- A source sample that is **stale** (its annotation predates a major VEP bundle upgrade) blocks the merge with a 423 response. Re-annotate the source first, then retry the merge.

---

## See also

- [Usage guide](usage-guide.md) — single-sample features (variant explorer, analysis modules, exports).
- [AncestryDNA Integration Plan §9 (Individuals)](AncestryDNA_Integration_Plan.md#9-individuals-model) — data model for grouping samples.
- [AncestryDNA Integration Plan §10 (Sample Merging)](AncestryDNA_Integration_Plan.md#10-sample-merging) — full technical contract for the merge layer.
