/** Ancestry module page (P3-27, P3-34, AMv2 Step 5).
 *
 * Layout:
 * - Ancestry result summary card (top population, confidence, coverage, evidence)
 * - Admixture bar chart (7 populations, NNLS fractions)
 * - PCA scatter plot (user projected onto reference panel, PC selection)
 * - Analysis details (collapsible — AIMs, PCs, method, reference panel)
 * - Chromosome painting section (LAI bundle states)
 * - Haplogroup assignments with traversal path (P3-34)
 */

import { useSearchParams } from "react-router-dom"
import { Download, Globe, Info, Loader2, Play } from "lucide-react"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import { useAncestryFindings, useHaplogroups, useLAIStatus, usePCACoordinates } from "@/api/ancestry"
import { useTriggerDownload } from "@/api/setup"
import AncestryResultCard from "@/components/ancestry/AncestryResultCard"
import AdmixtureBar from "@/components/ancestry/AdmixtureBar"
import PCAScatter from "@/components/ancestry/PCAScatter"
import HaplogroupCard from "@/components/ancestry/HaplogroupCard"
import AnalysisDetails from "@/components/ancestry/AnalysisDetails"

export default function AncestryView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const findingsQuery = useAncestryFindings(sampleId)
  const pcaQuery = usePCACoordinates(sampleId)
  const haplogroupQuery = useHaplogroups(sampleId)
  const laiStatusQuery = useLAIStatus()
  const triggerDownload = useTriggerDownload()

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Ancestry</h1>
        <PageEmpty icon={Globe} title="Select a sample to view ancestry results." />
      </div>
    )
  }

  const isLoading = findingsQuery.isLoading
  const hasError = findingsQuery.isError

  return (
    <div className="p-6">
      {/* Page header */}
      <div className="flex items-center gap-3 mb-6">
        <div
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-lg",
            "bg-primary/10 text-primary",
          )}
        >
          <Globe className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Ancestry</h1>
          <p className="text-sm text-muted-foreground">
            Ancestry inference via PCA projection and NNLS admixture estimation
          </p>
        </div>
      </div>

      {/* Loading state */}
      {isLoading && (
        <PageLoading message="Loading ancestry data..." />
      )}

      {/* Error state */}
      {hasError && !isLoading && (
        <PageError
          message={findingsQuery.error instanceof Error ? findingsQuery.error.message : "An unexpected error occurred."}
          onRetry={() => { findingsQuery.refetch(); }}
        />
      )}

      {/* No results yet */}
      {!isLoading && !hasError && !findingsQuery.data && (
        <PageEmpty
          icon={Globe}
          title="No ancestry results yet."
          description="Run the annotation pipeline to generate ancestry results."
        />
      )}

      {/* Main content */}
      {!isLoading && !hasError && findingsQuery.data && (
        <>
          {/* Ancestry Result Summary */}
          <section aria-label="Ancestry inference summary" className="mb-8">
            <AncestryResultCard finding={findingsQuery.data} />
          </section>

          {/* Admixture Bar Chart */}
          <section aria-label="Admixture proportions" className="mb-8">
            <div className="rounded-lg border bg-card p-5">
              <h2 className="text-lg font-semibold mb-3">Admixture Proportions</h2>
              <p className="text-sm text-muted-foreground mb-4">
                Estimated ancestry proportions using NNLS against reference population centroids
              </p>
              <AdmixtureBar admixture_fractions={findingsQuery.data.admixture_fractions} />
            </div>
          </section>

          {/* PCA Scatter Plot */}
          <section aria-label="PCA scatter plot" className="mb-8">
            <div className="rounded-lg border bg-card p-5">
              <h2 className="text-lg font-semibold mb-3">PCA Projection</h2>
              <p className="text-sm text-muted-foreground mb-4">
                Your sample projected onto the reference panel PCA space
              </p>
              {pcaQuery.isLoading && (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              )}
              {pcaQuery.isError && (
                <div className="text-sm text-destructive">
                  Failed to load PCA coordinates.
                </div>
              )}
              {pcaQuery.data && (
                <PCAScatter pcaData={pcaQuery.data} />
              )}
              {!pcaQuery.isLoading && !pcaQuery.isError && !pcaQuery.data && (
                <div className="text-sm text-muted-foreground text-center py-8">
                  PCA coordinates not available.
                </div>
              )}
            </div>
          </section>

          {/* Analysis Details (collapsible) */}
          <section aria-label="Analysis details" className="mb-8">
            <AnalysisDetails finding={findingsQuery.data} />
          </section>

          {/* Chromosome Painting Section */}
          <section aria-label="Chromosome painting" className="mb-8">
            <div className="rounded-lg border bg-card p-5">
              <h2 className="text-lg font-semibold mb-3">Chromosome Painting</h2>
              {laiStatusQuery.isLoading && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Checking availability...
                </div>
              )}
              {laiStatusQuery.isError && (
                <p className="text-sm text-destructive">
                  Failed to check LAI availability.
                </p>
              )}
              {laiStatusQuery.data && !laiStatusQuery.data.bundle_downloaded && (
                <div className="space-y-3">
                  <p className="text-sm text-muted-foreground">
                    Enable chromosome-level ancestry painting for detailed per-chromosome ancestry breakdown.
                    Requires a one-time ~500 MB download and Java 8+. Analysis takes 15-30 minutes.
                  </p>
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      disabled={triggerDownload.isPending}
                      onClick={() => triggerDownload.mutate(["lai_bundle"])}
                      className={cn(
                        "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
                        "bg-primary text-primary-foreground hover:bg-primary/90 transition-colors",
                        "disabled:opacity-50 disabled:cursor-not-allowed",
                      )}
                    >
                      <Download className="h-4 w-4" />
                      {triggerDownload.isPending ? "Starting..." : "Enable Chromosome Painting (~500 MB)"}
                    </button>
                  </div>
                </div>
              )}
              {laiStatusQuery.data && laiStatusQuery.data.bundle_downloaded && !laiStatusQuery.data.java_available && (
                <div className="flex items-start gap-2 rounded-md bg-amber-500/10 border border-amber-500/30 px-4 py-3">
                  <Info className="h-4 w-4 text-amber-500 mt-0.5 flex-shrink-0" />
                  <p className="text-sm text-amber-700 dark:text-amber-400">
                    Java 8+ is required for chromosome-level ancestry analysis. Please install Java and restart.
                  </p>
                </div>
              )}
              {laiStatusQuery.data && laiStatusQuery.data.lai_available && (
                <div className="space-y-3">
                  <p className="text-sm text-muted-foreground">
                    LAI bundle is ready. Run chromosome painting to see detailed per-chromosome ancestry breakdown.
                  </p>
                  <button
                    type="button"
                    disabled
                    title="Chromosome painting will be available in a future update"
                    className={cn(
                      "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium",
                      "bg-primary text-primary-foreground hover:bg-primary/90 transition-colors",
                      "disabled:opacity-50 disabled:cursor-not-allowed",
                    )}
                  >
                    <Play className="h-4 w-4" />
                    Run Chromosome Painting Analysis (~20 min)
                  </button>
                </div>
              )}
            </div>
          </section>

          {/* Haplogroup Assignments (P3-34) */}
          <section aria-label="Haplogroup assignments">
            {haplogroupQuery.isLoading && (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            )}
            {haplogroupQuery.isError && (
              <div className="text-sm text-destructive">
                Failed to load haplogroup data.
              </div>
            )}
            {haplogroupQuery.data && (
              <HaplogroupCard assignments={haplogroupQuery.data.assignments} />
            )}
          </section>
        </>
      )}
    </div>
  )
}
