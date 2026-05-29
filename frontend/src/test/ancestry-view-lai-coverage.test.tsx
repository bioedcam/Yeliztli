/** Tests for the LAI coverage telemetry surface in AncestryView (Step 24).
 *
 * Plan §6.7 covers four user-visible cases:
 *
 *  1. **Single-source** — unmerged AncestryDNA sample with a one-key
 *     `{"ancestrydna": {hits, drops}}` payload. Renders the "X of Y rsIDs
 *     mapped to bundle (Z% dropout)" summary and never renders the
 *     per-source breakdown table.
 *  2. **Merged** — three-key `{S1, S2, both}` payload. Renders the summary
 *     plus the three-row breakdown table.
 *  3. **High dropout** — `drop_rate_warning=true` fires a sonner toast and
 *     also renders the inline amber banner. Toast `id` is sample-scoped.
 *  4. **Empty / null** — telemetry omitted from the API response → panel
 *     does not render; telemetry present but all buckets zero → panel
 *     emits the "telemetry not available" fallback, no summary line.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "./test-utils"
import LAICoverageTelemetryPanel from "@/components/ancestry/LAICoverageTelemetryPanel"
import type { LAICoverageTelemetry } from "@/types/ancestry"

const toastWarningMock = vi.hoisted(() => vi.fn())

vi.mock("sonner", () => ({
  toast: {
    warning: toastWarningMock,
  },
  Toaster: () => null,
}))

const SINGLE_SOURCE_TELEMETRY: LAICoverageTelemetry = {
  per_source: {
    ancestrydna: { hits: 480, drops: 20 },
  },
  total_hits: 480,
  total_drops: 20,
  drop_rate: 0.04,
  drop_rate_warning: false,
}

const MERGED_TELEMETRY: LAICoverageTelemetry = {
  per_source: {
    S1: { hits: 400, drops: 30 },
    S2: { hits: 350, drops: 20 },
    both: { hits: 200, drops: 0 },
  },
  total_hits: 950,
  total_drops: 50,
  drop_rate: 0.05,
  drop_rate_warning: false,
}

const HIGH_DROPOUT_TELEMETRY: LAICoverageTelemetry = {
  per_source: {
    ancestrydna: { hits: 300, drops: 200 },
  },
  total_hits: 300,
  total_drops: 200,
  drop_rate: 0.4,
  drop_rate_warning: true,
}

const ZERO_TELEMETRY: LAICoverageTelemetry = {
  per_source: {},
  total_hits: 0,
  total_drops: 0,
  drop_rate: 0.0,
  drop_rate_warning: false,
}

describe("LAICoverageTelemetryPanel — single-source", () => {
  beforeEach(() => {
    toastWarningMock.mockClear()
  })
  afterEach(() => {
    cleanup()
  })

  it("renders the X-of-Y mapped summary line for AncestryDNA", () => {
    render(<LAICoverageTelemetryPanel telemetry={SINGLE_SOURCE_TELEMETRY} sampleId={42} />)
    const summary = screen.getByTestId("lai-coverage-summary")
    expect(summary).toHaveTextContent("480 of 500")
    expect(summary).toHaveTextContent("AncestryDNA rsIDs")
    expect(summary).toHaveTextContent("4.0% dropout")
  })

  it("does not render the merged breakdown table for unmerged samples", () => {
    render(<LAICoverageTelemetryPanel telemetry={SINGLE_SOURCE_TELEMETRY} sampleId={42} />)
    expect(screen.queryByTestId("lai-coverage-merged-table")).not.toBeInTheDocument()
  })

  it("does not fire the high-dropout toast for normal drop rates", () => {
    render(<LAICoverageTelemetryPanel telemetry={SINGLE_SOURCE_TELEMETRY} sampleId={42} />)
    expect(toastWarningMock).not.toHaveBeenCalled()
  })

  it("renders 23andMe label when telemetry is keyed by 23andme", () => {
    const telemetry: LAICoverageTelemetry = {
      ...SINGLE_SOURCE_TELEMETRY,
      per_source: { "23andme": { hits: 600, drops: 5 } },
      total_hits: 600,
      total_drops: 5,
    }
    render(<LAICoverageTelemetryPanel telemetry={telemetry} sampleId={1} />)
    expect(screen.getByTestId("lai-coverage-summary")).toHaveTextContent("23andMe rsIDs")
  })
})

describe("LAICoverageTelemetryPanel — merged sample", () => {
  beforeEach(() => {
    toastWarningMock.mockClear()
  })
  afterEach(() => {
    cleanup()
  })

  it("renders the three-row source-breakdown table", () => {
    render(<LAICoverageTelemetryPanel telemetry={MERGED_TELEMETRY} sampleId={42} />)
    const table = screen.getByTestId("lai-coverage-merged-table")
    expect(table).toBeInTheDocument()
    expect(screen.getByTestId("lai-coverage-row-S1")).toBeInTheDocument()
    expect(screen.getByTestId("lai-coverage-row-S2")).toBeInTheDocument()
    expect(screen.getByTestId("lai-coverage-row-both")).toBeInTheDocument()
  })

  it("renders the per-row mapped / dropped counts and per-row dropout", () => {
    render(<LAICoverageTelemetryPanel telemetry={MERGED_TELEMETRY} sampleId={42} />)
    const s1Row = screen.getByTestId("lai-coverage-row-S1")
    expect(s1Row).toHaveTextContent("Source 1")
    expect(s1Row).toHaveTextContent("400")
    expect(s1Row).toHaveTextContent("30")
    // S1 dropout = 30 / 430 ≈ 7.0%
    expect(s1Row).toHaveTextContent("7.0%")

    const bothRow = screen.getByTestId("lai-coverage-row-both")
    expect(bothRow).toHaveTextContent("Both sources")
    expect(bothRow).toHaveTextContent("200")
    // Both dropout = 0 / 200 = 0.0%
    expect(bothRow).toHaveTextContent("0.0%")
  })

  it("uses 'across all sources' in the summary line for merged payloads", () => {
    render(<LAICoverageTelemetryPanel telemetry={MERGED_TELEMETRY} sampleId={42} />)
    const summary = screen.getByTestId("lai-coverage-summary")
    expect(summary).toHaveTextContent("950 of 1,000")
    expect(summary).toHaveTextContent("across all sources")
  })
})

describe("LAICoverageTelemetryPanel — high dropout", () => {
  beforeEach(() => {
    toastWarningMock.mockClear()
  })
  afterEach(() => {
    cleanup()
  })

  it("fires a sonner toast.warning when drop_rate_warning is true", () => {
    render(<LAICoverageTelemetryPanel telemetry={HIGH_DROPOUT_TELEMETRY} sampleId={42} />)
    expect(toastWarningMock).toHaveBeenCalledTimes(1)
    const [message, opts] = toastWarningMock.mock.calls[0]
    expect(message).toMatch(/Reduced LAI coverage/i)
    expect(message).toMatch(/40\.0%/)
    expect(opts).toMatchObject({
      id: "lai-coverage-42",
    })
    expect(opts.description).toMatch(/v2\.0\.0/)
  })

  it("renders the inline amber warning banner alongside the toast", () => {
    render(<LAICoverageTelemetryPanel telemetry={HIGH_DROPOUT_TELEMETRY} sampleId={42} />)
    const banner = screen.getByTestId("lai-coverage-warning")
    expect(banner).toHaveTextContent(/Reduced LAI coverage/)
    expect(banner).toHaveTextContent("40.0%")
  })

  it("uses a stable per-sample toast id so re-renders dedupe", () => {
    const { rerender } = render(
      <LAICoverageTelemetryPanel telemetry={HIGH_DROPOUT_TELEMETRY} sampleId={42} />,
    )
    rerender(<LAICoverageTelemetryPanel telemetry={HIGH_DROPOUT_TELEMETRY} sampleId={42} />)
    // Both calls share the same id; sonner internally dedupes — we still
    // expect the same id every time so a downstream Toaster collapses them.
    for (const call of toastWarningMock.mock.calls) {
      expect(call[1].id).toBe("lai-coverage-42")
    }
  })

  it("falls back to a generic toast id when sampleId is null", () => {
    render(<LAICoverageTelemetryPanel telemetry={HIGH_DROPOUT_TELEMETRY} sampleId={null} />)
    const [, opts] = toastWarningMock.mock.calls[0]
    expect(opts.id).toBe("lai-coverage-warning")
  })
})

describe("LAICoverageTelemetryPanel — empty / null telemetry", () => {
  beforeEach(() => {
    toastWarningMock.mockClear()
  })
  afterEach(() => {
    cleanup()
  })

  it("renders the not-available fallback when both totals are zero", () => {
    render(<LAICoverageTelemetryPanel telemetry={ZERO_TELEMETRY} sampleId={42} />)
    expect(screen.getByTestId("lai-coverage-telemetry-empty")).toBeInTheDocument()
    expect(screen.queryByTestId("lai-coverage-summary")).not.toBeInTheDocument()
    expect(screen.queryByTestId("lai-coverage-merged-table")).not.toBeInTheDocument()
    expect(toastWarningMock).not.toHaveBeenCalled()
  })

  it("does not fire the toast on the zero-bucket fallback", () => {
    const allZero: LAICoverageTelemetry = {
      ...ZERO_TELEMETRY,
      drop_rate_warning: true, // even with the flag set, no rsIDs → no toast
    }
    render(<LAICoverageTelemetryPanel telemetry={allZero} sampleId={42} />)
    expect(toastWarningMock).not.toHaveBeenCalled()
  })
})
