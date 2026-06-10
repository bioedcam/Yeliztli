/** Pharmacogenomics API types (P3-06). */

/** Three-state calling confidence for star-allele calls. */
export type CallConfidence = "Complete" | "Partial" | "Insufficient"

/** Per-gene star-allele summary for metabolizer cards. */
export interface GeneSummary {
  gene: string
  diplotype: string | null
  phenotype: string | null
  call_confidence: CallConfidence | null
  confidence_note: string | null
  activity_score: number | null
  ehr_notation: string | null
  evidence_level: number | null
  involved_rsids: string[]
  drugs: string[]
  /**
   * Gene-specific interpretive caveat (context only — never changes the
   * metabolizer status or evidence level). Mirrors the backend constant
   * `DPYD_FLUOROPYRIMIDINE_CAVEAT` in backend/disclaimers.py (SW-E5).
   */
  gene_caveat: string | null
}

export interface GeneSummaryResponse {
  items: GeneSummary[]
  total: number
}

/** Drug list item from CPIC database. */
export interface DrugListItem {
  drug: string
  genes: string[]
  classification: string | null
}

export interface DrugListResponse {
  items: DrugListItem[]
  total: number
}

/** Per-gene genotype effect for a specific drug. */
export interface GeneEffect {
  gene: string
  diplotype: string | null
  metabolizer_status: string | null
  recommendation: string | null
  classification: string | null
  guideline_url: string | null
  call_confidence: CallConfidence | null
  confidence_note: string | null
  evidence_level: number | null
  activity_score: number | null
  ehr_notation: string | null
  involved_rsids: string[]
  /** See {@link GeneSummary.gene_caveat}. Mirrors a backend disclaimer (SW-E5). */
  gene_caveat: string | null
}

export interface DrugLookupResponse {
  drug: string
  gene_effects: GeneEffect[]
}
