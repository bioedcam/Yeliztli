/** Tests for <StaleSampleGate> (Step 14, Plan §7.5).
 *
 * Covers:
 * - 423 response → full-page banner with payload-driven versions + CTA
 * - 2xx response → children render through
 * - No sample_id in URL → children render through without a probe
 * - CTA fires POST to the payload's `reannotate_url`
 * - Re-annotation error surfaces in the banner without removing the gate
 */

import type { ReactNode } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import StaleSampleGate from '@/components/layout/StaleSampleGate'

const STALE_PAYLOAD = {
  installed_version: 'v1.0.0',
  required_version: 'v2.0.0',
  update_url: 'https://example.invalid/bundle-v2',
  reannotate_url: '/api/annotation/42',
}

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockReset()
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function createWrapper(initialEntries: string[] = ['/?sample_id=42']) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
}

function stalenessMock({ status, body }: { status: number; body: unknown }) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  })
}

describe('StaleSampleGate', () => {
  it('renders the banner with payload-driven versions when the API returns 423', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url.startsWith('/api/variants/count')) {
        return stalenessMock({ status: 423, body: { detail: STALE_PAYLOAD } })
      }
      return stalenessMock({ status: 200, body: { total: 0 } })
    })

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">protected content</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    expect(await screen.findByTestId('stale-sample-gate')).toBeInTheDocument()
    expect(screen.queryByTestId('protected-content')).not.toBeInTheDocument()
    expect(screen.getByTestId('stale-installed-version')).toHaveTextContent('v1.0.0')
    expect(screen.getByTestId('stale-required-version')).toHaveTextContent('v2.0.0')
    expect(screen.getByRole('link', { name: /view bundle update/i })).toHaveAttribute(
      'href',
      STALE_PAYLOAD.update_url,
    )
  })

  it('renders children when the staleness probe returns 200', async () => {
    mockFetch.mockImplementation(() =>
      stalenessMock({ status: 200, body: { total: 12345 } }),
    )

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">protected content</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    expect(await screen.findByTestId('protected-content')).toBeInTheDocument()
    expect(screen.queryByTestId('stale-sample-gate')).not.toBeInTheDocument()
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/variants/count?sample_id=42')
    })
  })

  it('renders children without probing when no sample_id is in the URL', async () => {
    mockFetch.mockImplementation(() => stalenessMock({ status: 200, body: {} }))

    render(
      <StaleSampleGate>
        <div data-testid="protected-content">no sample</div>
      </StaleSampleGate>,
      { wrapper: createWrapper(['/dashboard']) },
    )

    expect(await screen.findByTestId('protected-content')).toBeInTheDocument()
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('CTA fires POST to the payload reannotate_url and surfaces success state', async () => {
    mockFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (
        typeof url === 'string' &&
        url === STALE_PAYLOAD.reannotate_url &&
        init?.method === 'POST'
      ) {
        return stalenessMock({
          status: 202,
          body: { job_id: 'job-123', sample_id: 42, status: 'pending' },
        })
      }
      if (typeof url === 'string' && url.startsWith('/api/variants/count')) {
        return stalenessMock({ status: 423, body: { detail: STALE_PAYLOAD } })
      }
      return stalenessMock({ status: 200, body: {} })
    })

    render(
      <StaleSampleGate>
        <div>hidden</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    const cta = await screen.findByTestId('stale-reannotate-cta')
    fireEvent.click(cta)

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        STALE_PAYLOAD.reannotate_url,
        expect.objectContaining({ method: 'POST' }),
      )
    })
    expect(await screen.findByTestId('stale-success')).toBeInTheDocument()
  })

  it('surfaces a re-annotation error and keeps the gate visible', async () => {
    mockFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (
        typeof url === 'string' &&
        url === STALE_PAYLOAD.reannotate_url &&
        init?.method === 'POST'
      ) {
        return stalenessMock({ status: 500, body: { detail: 'annotator unavailable' } })
      }
      if (typeof url === 'string' && url.startsWith('/api/variants/count')) {
        return stalenessMock({ status: 423, body: { detail: STALE_PAYLOAD } })
      }
      return stalenessMock({ status: 200, body: {} })
    })

    render(
      <StaleSampleGate>
        <div>hidden</div>
      </StaleSampleGate>,
      { wrapper: createWrapper() },
    )

    const cta = await screen.findByTestId('stale-reannotate-cta')
    fireEvent.click(cta)

    const errorNode = await screen.findByTestId('stale-error')
    expect(errorNode).toHaveTextContent(/annotator unavailable/i)
    expect(screen.getByTestId('stale-sample-gate')).toBeInTheDocument()
  })
})
