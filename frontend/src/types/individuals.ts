/** Individual / linked-sample types matching backend Pydantic models
 * (Step 47 / IND-03; Plan §9.2, §9.3). */

export type BiologicalSex = "XX" | "XY"

export interface LinkedSample {
  id: number
  name: string
  file_format: string | null
  vendor: string | null
  created_at: string | null
  updated_at: string | null
}

export interface IndividualSummary {
  id: number
  display_name: string
  notes: string | null
  biological_sex: BiologicalSex | null
  created_at: string | null
  updated_at: string | null
  sample_count: number
  vendors: string[]
  last_activity: string | null
}

export interface IndividualDetail {
  id: number
  display_name: string
  notes: string | null
  biological_sex: BiologicalSex | null
  created_at: string | null
  updated_at: string | null
  linked_samples: LinkedSample[]
  aggregated_findings_count: number
}

export interface IndividualCreate {
  display_name: string
  notes?: string | null
  biological_sex?: BiologicalSex | null
}

export interface IndividualUpdate {
  display_name?: string
  notes?: string | null
  biological_sex?: BiologicalSex | null
}

/** 409 body returned by POST /individuals/{id}/link-sample when the
 * target sample is already linked to a different individual. */
export interface LinkConflictDetail {
  sample_id: number
  individual_id: number
  individual_display_name: string
  message: string
}

export class IndividualsApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, message: string, body: unknown) {
    super(message)
    this.name = "IndividualsApiError"
    this.status = status
    this.body = body
  }

  /** Convenience predicate for the 409 link-elsewhere case. */
  isLinkConflict(): this is IndividualsApiError & { body: { detail: LinkConflictDetail } } {
    if (this.status !== 409) return false
    const body = this.body as { detail?: unknown } | null
    if (!body || typeof body !== "object") return false
    const detail = body.detail
    return (
      typeof detail === "object" &&
      detail !== null &&
      "sample_id" in detail &&
      "individual_id" in detail
    )
  }
}
