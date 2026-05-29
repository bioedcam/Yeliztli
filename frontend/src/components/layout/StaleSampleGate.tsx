/** Full-page gate that blocks analysis pages when the active sample is stale.
 *
 * Probes a representative gated endpoint (`/api/variants/count`) for the
 * URL-scoped sample. When the route returns HTTP 423 the gate parses the
 * payload (`installed_version`, `required_version`, `update_url`,
 * `reannotate_url` — Plan §7.5) and renders a full-page banner whose single
 * CTA fires `POST` against the payload's `reannotate_url` (the existing
 * `POST /api/annotation/{sample_id}` escape hatch). Any other status — 2xx,
 * 4xx other than 423, network error — lets `children` through; this gate is
 * concerned only with the staleness contract and never blocks on unrelated
 * failures.
 */

import { useEffect, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { parseSampleId } from '@/lib/format'
import { cn } from '@/lib/utils'

export interface StalenessPayload {
  installed_version: string
  required_version: string
  update_url: string
  reannotate_url: string
}

interface StaleSampleGateProps {
  children: ReactNode
}

async function probeStaleness(sampleId: number): Promise<StalenessPayload | null> {
  const res = await fetch(`/api/variants/count?sample_id=${sampleId}`)
  if (res.status !== 423) return null
  const body = (await res.json().catch(() => null)) as { detail?: StalenessPayload } | null
  return body?.detail ?? null
}

export default function StaleSampleGate({ children }: StaleSampleGateProps) {
  const [searchParams] = useSearchParams()
  const activeSampleId = parseSampleId(searchParams.get('sample_id'))
  const queryClient = useQueryClient()

  const { data: stale, isPending } = useQuery<StalenessPayload | null>({
    queryKey: ['sample-staleness', activeSampleId],
    queryFn: () => probeStaleness(activeSampleId as number),
    enabled: activeSampleId != null,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  })

  const reannotate = useMutation({
    mutationFn: async (url: string) => {
      const res = await fetch(url, { method: 'POST' })
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        const detail = (body as { detail?: string } | null)?.detail
        throw new Error(detail ?? `Re-annotation failed: ${res.status}`)
      }
      return res.json() as Promise<{ job_id: string; sample_id: number; status: string }>
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sample-staleness', activeSampleId] })
    },
  })

  useEffect(() => {
    reannotate.reset()
    // Reset the mutation banner state when the active sample changes so
    // a prior success/error toast from a different sample doesn't leak in.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSampleId])

  // While an active sample's staleness probe is still pending, hold back
  // children so potentially-stale content never flashes before the gate can
  // activate. Once the probe resolves to a non-stale value, render children.
  if (activeSampleId != null && isPending) {
    return null
  }

  if (!stale) {
    return <>{children}</>
  }

  const banner = (
    <section
      role="alert"
      aria-live="polite"
      data-testid="stale-sample-gate"
      className="p-6 max-w-3xl mx-auto"
    >
      <div
        className={cn(
          'flex flex-col gap-4 rounded-lg border p-6',
          'border-amber-200 bg-amber-50 text-amber-900',
          'dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100',
        )}
      >
        <div className="flex items-start gap-3">
          <AlertTriangle className="h-6 w-6 shrink-0 mt-0.5" aria-hidden="true" />
          <div className="space-y-1">
            <h2 className="text-base font-semibold">Sample requires re-annotation</h2>
            <p className="text-sm">
              This sample was annotated against bundle{' '}
              <strong data-testid="stale-installed-version">{stale.installed_version}</strong>;
              re-annotate against{' '}
              <strong data-testid="stale-required-version">{stale.required_version}</strong>{' '}
              to view results.
            </p>
          </div>
        </div>

        {reannotate.isError ? (
          <p
            role="status"
            data-testid="stale-error"
            className="text-sm text-red-700 dark:text-red-300"
          >
            {reannotate.error instanceof Error
              ? reannotate.error.message
              : 'Re-annotation failed.'}
          </p>
        ) : null}

        {reannotate.isSuccess ? (
          <p
            role="status"
            data-testid="stale-success"
            className="text-sm text-emerald-700 dark:text-emerald-300"
          >
            Re-annotation started. The gate will lift once the job completes.
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            data-testid="stale-reannotate-cta"
            disabled={reannotate.isPending || reannotate.isSuccess}
            onClick={() => reannotate.mutate(stale.reannotate_url)}
            className={cn(
              'inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium',
              'bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-60',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
              'dark:bg-amber-500 dark:hover:bg-amber-400 dark:text-amber-950',
            )}
          >
            <RefreshCw
              className={cn('h-4 w-4', reannotate.isPending && 'animate-spin')}
              aria-hidden="true"
            />
            {reannotate.isPending ? 'Starting re-annotation…' : 'Re-annotate sample'}
          </button>
          {stale.update_url ? (
            <a
              href={stale.update_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm underline hover:no-underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary rounded"
            >
              View bundle update
            </a>
          ) : null}
        </div>
      </div>
    </section>
  )

  return banner
}
