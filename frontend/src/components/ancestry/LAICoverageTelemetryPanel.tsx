/** LAI per-source rsID coverage telemetry surface (Step 24, Plan §6.7).
 *
 * Renders one of two shapes depending on the payload:
 *
 *  - **Single-source** (unmerged samples): a single "X of Y &lt;vendor&gt;
 *    rsIDs mapped to bundle (Z% dropout)" sentence — the AncestryDNA
 *    coverage gap on the pre-v2.0.0 bundle is the user-facing surface
 *    for R-15a (Plan §1.3, §6.6).
 *  - **Merged** (`S1` / `S2` / `both` payload from Plan §10.2): the same
 *    summary line plus a three-row breakdown table so the user can see
 *    per-source contributions.
 *
 * The `drop_rate_warning` flag (set by the runner when the drop rate
 * exceeds 15%) fires a sonner `toast.warning` once per result load.
 * The toast is keyed by sample id so navigating between samples re-arms
 * the warning instead of suppressing it (Plan §6.6).
 */

import { useEffect } from "react"
import { AlertTriangle, BarChart3 } from "lucide-react"
import { toast } from "sonner"
import type {
  LAICoverageSourceTelemetry,
  LAICoverageTelemetry,
} from "@/types/ancestry"
import { cn } from "@/lib/utils"

const MERGED_SOURCE_KEYS = ["S1", "S2", "both"] as const

const SOURCE_LABELS: Record<string, string> = {
  S1: "Source 1",
  S2: "Source 2",
  both: "Both sources",
  ancestrydna: "AncestryDNA",
  "23andme": "23andMe",
}

function isMergedTelemetry(telemetry: LAICoverageTelemetry): boolean {
  return MERGED_SOURCE_KEYS.every((key) => key in telemetry.per_source)
}

function totalForBucket(bucket: LAICoverageSourceTelemetry): number {
  return bucket.hits + bucket.drops
}

function formatPercent(numerator: number, denominator: number): string {
  if (denominator === 0) return "0.0%"
  return `${((numerator / denominator) * 100).toFixed(1)}%`
}

function vendorLabel(key: string): string {
  return SOURCE_LABELS[key] ?? key
}

interface LAICoverageTelemetryPanelProps {
  telemetry: LAICoverageTelemetry
  sampleId: number | null
}

export default function LAICoverageTelemetryPanel({
  telemetry,
  sampleId,
}: LAICoverageTelemetryPanelProps) {
  const totalRsIds = telemetry.total_hits + telemetry.total_drops
  const dropPct = formatPercent(telemetry.total_drops, totalRsIds)
  const merged = isMergedTelemetry(telemetry)
  const summaryLabel = merged ? "across all sources" : vendorLabel(Object.keys(telemetry.per_source)[0] ?? "")

  useEffect(() => {
    if (!telemetry.drop_rate_warning) return
    if (totalRsIds === 0) return
    const toastId = sampleId != null ? `lai-coverage-${sampleId}` : "lai-coverage-warning"
    toast.warning(
      `Reduced LAI coverage: ${dropPct} of rsIDs were dropped before chromosome painting.`,
      {
        id: toastId,
        description:
          "Update the LAI bundle to v2.0.0 to lift AncestryDNA dropout to parity with 23andMe.",
        duration: 10000,
      },
    )
  }, [telemetry.drop_rate_warning, dropPct, sampleId, totalRsIds])

  if (totalRsIds === 0) {
    return (
      <div
        data-testid="lai-coverage-telemetry-empty"
        className="rounded-md border border-dashed border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground"
      >
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4" aria-hidden="true" />
          LAI coverage telemetry is not available for this run.
        </div>
      </div>
    )
  }

  return (
    <section
      aria-label="LAI coverage telemetry"
      data-testid="lai-coverage-telemetry"
      className="space-y-3"
    >
      <div className="flex items-start gap-2 text-sm">
        <BarChart3 className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <p data-testid="lai-coverage-summary">
          <span className="font-medium">
            {telemetry.total_hits.toLocaleString()} of {totalRsIds.toLocaleString()}
          </span>{" "}
          {merged ? "rsIDs" : `${vendorLabel(Object.keys(telemetry.per_source)[0] ?? "")} rsIDs`}{" "}
          mapped to bundle ({dropPct} dropout {summaryLabel ? `— ${summaryLabel}` : ""}).
        </p>
      </div>

      {merged && (
        <div className="overflow-hidden rounded-md border border-border">
          <table
            data-testid="lai-coverage-merged-table"
            className="w-full text-sm"
            aria-label="Per-source LAI coverage breakdown"
          >
            <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2 text-left font-medium">Source</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Mapped</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Dropped</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Dropout</th>
              </tr>
            </thead>
            <tbody>
              {MERGED_SOURCE_KEYS.map((key) => {
                const bucket = telemetry.per_source[key] ?? { hits: 0, drops: 0 }
                const bucketTotal = totalForBucket(bucket)
                return (
                  <tr
                    key={key}
                    data-testid={`lai-coverage-row-${key}`}
                    className="border-t border-border"
                  >
                    <th scope="row" className="px-3 py-2 text-left font-medium">
                      {vendorLabel(key)}
                    </th>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {bucket.hits.toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {bucket.drops.toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {formatPercent(bucket.drops, bucketTotal)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {telemetry.drop_rate_warning && (
        <div
          data-testid="lai-coverage-warning"
          role="status"
          className={cn(
            "flex items-start gap-2 rounded-md px-3 py-2 text-sm",
            "border border-amber-500/30 bg-amber-500/10",
            "text-amber-800 dark:text-amber-300",
          )}
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>
            Reduced LAI coverage — {dropPct} of rsIDs were dropped before chromosome
            painting. Update the LAI bundle to v2.0.0 for full AncestryDNA coverage.
          </p>
        </div>
      )}
    </section>
  )
}
