/** Dashboard — main landing page with status bar, module cards, findings, and QC (P1-20).
 *
 * Layout: status bar (40px) → module cards grid → high-confidence findings → collapsible QC.
 * When no sample is active, shows upload prompt.
 */

import { useSearchParams } from 'react-router-dom'
import FileUpload from '@/components/upload/FileUpload'
import StatusBar from '@/components/dashboard/StatusBar'
import AnnotationPanel from '@/components/dashboard/AnnotationPanel'
import ModuleCardsGrid from '@/components/dashboard/ModuleCardsGrid'
import FindingsPreview from '@/components/dashboard/FindingsPreview'
import QualityControl from '@/components/dashboard/QualityControl'
import StaleSampleGate from '@/components/layout/StaleSampleGate'
import { useSamples } from '@/api/samples'
import { useTotalVariantCount, useQCStats } from '@/api/variants'
import { parseSampleId } from '@/lib/format'
import { Upload } from 'lucide-react'
import PageLoading from '@/components/ui/PageLoading'

export default function Dashboard() {
  const [searchParams] = useSearchParams()
  const activeSampleId = parseSampleId(searchParams.get('sample_id'))

  const { data: samples, isLoading } = useSamples()
  const activeSample = samples?.find((s) => s.id === activeSampleId)

  const { data: variantCount } = useTotalVariantCount(activeSampleId)
  const { data: qcStats } = useQCStats(activeSampleId)

  // ── Loading state: avoid flash of upload prompt ───────────

  if (isLoading && activeSampleId) {
    return <PageLoading message="Loading dashboard..." />
  }

  // ── No active sample: show upload prompt ──────────────────

  if (!activeSample) {
    return (
      <div className="p-6 max-w-4xl mx-auto">
        <h1 className="text-2xl font-bold text-foreground">Dashboard</h1>
        <div className="mt-8 flex flex-col items-center text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
            <Upload className="h-7 w-7 text-primary" />
          </div>
          <h2 className="mt-4 text-lg font-semibold text-foreground">
            Get Started
          </h2>
          <p className="mt-2 text-sm text-muted-foreground max-w-md">
            Upload a 23andMe raw data file to begin exploring your genome.
          </p>
          <div className="mt-6 w-full max-w-md">
            <FileUpload />
          </div>
        </div>
      </div>
    )
  }

  // ── Active sample: full dashboard layout ──────────────────

  return (
    <StaleSampleGate>
      <div className="p-6 max-w-5xl mx-auto space-y-6">
        {/* Status bar */}
        <StatusBar
          sample={activeSample}
          variantCount={variantCount ?? null}
        />

        {/* Annotation panel */}
        <AnnotationPanel sampleId={activeSample.id} variantCount={variantCount ?? null} />

        {/* Module cards grid */}
        <ModuleCardsGrid sampleId={activeSample.id} />

        {/* High-confidence findings */}
        <FindingsPreview sampleId={activeSample.id} />

        {/* Collapsible QC */}
        <QualityControl variantCount={variantCount ?? null} qcStats={qcStats ?? null} />
      </div>
    </StaleSampleGate>
  )
}
