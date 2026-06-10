/** Non-dismissible confirm-in-CLIA gate for actionable P/LP findings
 * (SW-A1 / roadmap #10), §12.10.
 *
 * Shown whenever Pathogenic / Likely-pathogenic findings are present, to make
 * clear they are array-derived and must be confirmed in a CLIA/accredited lab
 * with genetic counseling before any medical action — mirroring the APOE-gate
 * framing.
 *
 * Canonical source of the wording: the backend `CLIA_CONFIRMATION` constant in
 * `backend/analysis/return_framing.py`. This card is its reader-facing register;
 * keep the two in sync. It is deliberately **non-dismissible** — there is no
 * close control, so it cannot be hidden.
 */

import { ShieldAlert } from "lucide-react"

export default function ClinicalConfirmationGate() {
  return (
    <div
      role="note"
      aria-label="Clinical confirmation required before acting on these results"
      data-testid="clia-confirmation-gate"
      className="mb-4 rounded-md border-2 border-rose-300 bg-rose-50 p-3 dark:border-rose-800 dark:bg-rose-950/30"
    >
      <div className="flex items-start gap-2">
        <ShieldAlert
          className="mt-0.5 h-5 w-5 shrink-0 text-rose-600 dark:text-rose-400"
          aria-hidden="true"
        />
        <div className="text-rose-900 dark:text-rose-200">
          <p className="mb-1 text-sm font-semibold">
            Confirm before acting — not a clinical diagnosis
          </p>
          {/* Verbatim copy of the backend CLIA_CONFIRMATION constant — keep identical. */}
          <p className="text-xs leading-relaxed">
            This is an array-derived research/educational result, not a clinical diagnosis.
            Genotyping-array calls can be wrong, especially for rare variants. Before any
            medical decision, confirm an actionable result in a CLIA/accredited laboratory
            and review it with a genetic counselor or clinician.
          </p>
        </div>
      </div>
    </div>
  )
}
