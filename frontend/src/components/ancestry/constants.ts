/** Ancestry module shared constants (P3-27, AMv2 Step 5).
 *
 * Canonical 7-population order: AFR, AMR, CSA, EAS, EUR, MID, OCE.
 * Updated from 6 populations (SAS→CSA rename, MID addition).
 */

/** Population code → display label mapping. */
export const POPULATION_LABELS: Record<string, string> = {
  AFR: "African",
  AMR: "Admixed American",
  CSA: "Central/South Asian",
  EAS: "East Asian",
  EUR: "European",
  MID: "Middle Eastern",
  OCE: "Oceanian",
}

/** Population code → color mapping for charts. */
export const POPULATION_COLORS: Record<string, string> = {
  AFR: "#F59E0B",  // amber-500
  AMR: "#EF4444",  // red-500
  CSA: "#8B5CF6",  // violet-500
  EAS: "#10B981",  // emerald-500
  EUR: "#3B82F6",  // blue-500
  MID: "#14B8A6",  // teal-500
  OCE: "#EC4899",  // pink-500
}

/** Canonical population order for consistent display. */
export const POPULATION_ORDER = ["AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"] as const
