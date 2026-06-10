/** Pharmacogenomics module page (P3-06).
 *
 * Displays metabolizer phenotype cards with three-state indicators,
 * a searchable drug interaction table, and drug detail slide-in panel.
 *
 * PRD E2E flow F3: Dashboard -> Pharmacogenomics card -> drug table
 * visible with three-state indicators -> click drug -> detail view
 */

import { useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Pill } from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import { usePharmaGenes, usePharmaDrugs } from "@/api/pharmacogenomics"
import MetabolizerCard from "@/components/pharmacogenomics/MetabolizerCard"
import DrugTable from "@/components/pharmacogenomics/DrugTable"
import DrugDetailPanel from "@/components/pharmacogenomics/DrugDetailPanel"
import MedicationSafetyReport from "@/components/pharmacogenomics/MedicationSafetyReport"

export default function PharmacogenomicsView() {
  const [searchParams] = useSearchParams()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const [selectedDrug, setSelectedDrug] = useState<string | null>(null)

  const genesQuery = usePharmaGenes(sampleId)
  const drugsQuery = usePharmaDrugs()

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">Pharmacogenomics</h1>
        <PageEmpty icon={Pill} title="Select a sample to view pharmacogenomics results." />
      </div>
    )
  }

  const isLoading = genesQuery.isLoading || drugsQuery.isLoading
  const hasError = genesQuery.isError || drugsQuery.isError

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
          <Pill className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Pharmacogenomics</h1>
          <p className="text-sm text-muted-foreground">
            Drug-gene interactions and metabolizer status powered by CPIC guidelines
          </p>
        </div>
      </div>

      {/* Loading state */}
      {isLoading && <PageLoading message="Loading pharmacogenomics data..." />}

      {/* Error state */}
      {hasError && !isLoading && (
        <PageError
          message={
            genesQuery.error instanceof Error
              ? genesQuery.error.message
              : drugsQuery.error instanceof Error
                ? drugsQuery.error.message
                : "An unexpected error occurred."
          }
          onRetry={() => {
            genesQuery.refetch()
            drugsQuery.refetch()
          }}
        />
      )}

      {/* Main content */}
      {!isLoading && !hasError && (
        <>
          {/* Consolidated drug-centric medication-safety report (SW-E4) */}
          <MedicationSafetyReport sampleId={sampleId} />

          {/* Metabolizer phenotype cards */}
          {genesQuery.data && genesQuery.data.items.length > 0 && (
            <section aria-label="Metabolizer phenotype cards" className="mb-8">
              <h2 className="text-lg font-semibold mb-3">Gene Results</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                {genesQuery.data.items.map((gene) => (
                  <MetabolizerCard key={gene.gene} gene={gene} />
                ))}
              </div>
            </section>
          )}

          {/* Empty state for genes */}
          {genesQuery.data && genesQuery.data.items.length === 0 && (
            <div className="mb-8">
              <PageEmpty
                icon={Pill}
                title="No pharmacogenomics results yet."
                description="Run annotation to generate star-allele calls."
              />
            </div>
          )}

          {/* Drug interaction table */}
          {drugsQuery.data && (
            <section aria-label="Drug interactions" className="mb-6">
              <h2 className="text-lg font-semibold mb-3">Drug Interactions</h2>
              <DrugTable
                drugs={drugsQuery.data.items}
                onSelectDrug={setSelectedDrug}
                selectedDrug={selectedDrug}
              />
            </section>
          )}
        </>
      )}

      {/* Drug detail slide-in panel */}
      {selectedDrug && sampleId && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-30 bg-black/20"
            onClick={() => setSelectedDrug(null)}
            aria-hidden="true"
          />
          <DrugDetailPanel
            drugName={selectedDrug}
            sampleId={sampleId}
            onClose={() => setSelectedDrug(null)}
          />
        </>
      )}
    </div>
  )
}
