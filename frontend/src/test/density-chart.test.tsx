/** Tests for variant density histogram chart (P2-23).
 *
 * T2-21: Density histogram renders with correct bin counts.
 */

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from './test-utils'
import VariantDensityHistogram from '@/components/charts/VariantDensityHistogram'
import type { DensityBin } from '@/types/variants'

// Mock react-plotly.js since it requires a browser canvas. Expose each trace's
// name + y-values so tests can assert the DATA reaching the chart, not just the
// trace count — a regression that mis-stacked or dropped a series is invisible
// to a `data.length` check.
vi.mock('react-plotly.js', () => ({
  default: ({
    data,
    layout,
  }: {
    data: Array<{ name?: string; y?: number[] }>
    layout: { title?: { text?: string } }
  }) => (
    <div
      data-testid="plotly-chart"
      data-title={layout?.title?.text}
      data-traces={JSON.stringify(data.map((t) => ({ name: t.name, y: t.y })))}
    >
      <span data-testid="plotly-trace-count">{data.length}</span>
    </div>
  ),
}))

function traceMap(chart: HTMLElement): Record<string, number[]> {
  const traces = JSON.parse(chart.getAttribute('data-traces') ?? '[]') as Array<{
    name: string
    y: number[]
  }>
  return Object.fromEntries(traces.map((t) => [t.name, t.y]))
}

const MOCK_BINS: DensityBin[] = [
  { chrom: '1', bin_start: 0, bin_end: 1_000_000, high: 0, moderate: 5, low: 3, modifier: 10, total: 18 },
  { chrom: '1', bin_start: 1_000_000, bin_end: 2_000_000, high: 1, moderate: 2, low: 1, modifier: 8, total: 12 },
  { chrom: '2', bin_start: 0, bin_end: 1_000_000, high: 2, moderate: 3, low: 0, modifier: 5, total: 10 },
]

describe('VariantDensityHistogram', () => {
  it('renders a Plotly chart with 4 traces (modifier, low, moderate, high)', () => {
    render(<VariantDensityHistogram bins={MOCK_BINS} />)
    const chart = screen.getByTestId('plotly-chart')
    expect(chart).toBeInTheDocument()
    expect(chart.getAttribute('data-title')).toBe('Variant Density (per 1 Mb)')
    expect(screen.getByTestId('plotly-trace-count').textContent).toBe('4')

    // Each impact tier must carry its per-bin counts (the y-values), in bin
    // order — not merely exist as a trace.
    const y = traceMap(chart)
    expect(y['Modifier']).toEqual([10, 8, 5])
    expect(y['Low']).toEqual([3, 1, 0])
    expect(y['Moderate']).toEqual([5, 2, 3])
    expect(y['High']).toEqual([0, 1, 2])
  })

  it('shows empty state message when bins array is empty', () => {
    render(<VariantDensityHistogram bins={[]} />)
    expect(screen.getByText('No variant density data available.')).toBeInTheDocument()
    expect(screen.queryByTestId('plotly-chart')).not.toBeInTheDocument()
  })

  it('renders with single bin', () => {
    const singleBin: DensityBin[] = [
      { chrom: '1', bin_start: 0, bin_end: 1_000_000, high: 1, moderate: 0, low: 0, modifier: 0, total: 1 },
    ]
    render(<VariantDensityHistogram bins={singleBin} />)
    expect(screen.getByTestId('plotly-chart')).toBeInTheDocument()
  })

  it('renders with many bins across chromosomes', () => {
    const manyBins: DensityBin[] = Array.from({ length: 50 }, (_, i) => ({
      chrom: String(Math.floor(i / 5) + 1),
      bin_start: (i % 5) * 1_000_000,
      bin_end: (i % 5 + 1) * 1_000_000,
      high: i % 3,
      moderate: i % 5,
      low: i % 2,
      modifier: 10,
      total: (i % 3) + (i % 5) + (i % 2) + 10,
    }))
    render(<VariantDensityHistogram bins={manyBins} />)
    expect(screen.getByTestId('plotly-chart')).toBeInTheDocument()
  })
})
