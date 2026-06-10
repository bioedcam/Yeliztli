/** Tests for the Analysis Module Dashboard / Findings Explorer (P3-43). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render as rtlRender, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import FindingsExplorer from "@/pages/FindingsExplorer"
import type { Finding, FindingsSummaryResponse } from "@/types/findings"
import type { ReactElement, ReactNode } from "react"

// ── Custom render with initialEntries ────────────────────────────────

function renderWithRoute(ui: ReactElement, initialEntries: string[] = ["/"]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
  return rtlRender(ui, { wrapper: Wrapper })
}

// ── Mock data ────────────────────────────────────────────────────────

const SAMPLE_FINDINGS: Finding[] = [
  {
    id: 1,
    module: "cancer",
    category: "monogenic",
    evidence_level: 4,
    gene_symbol: "BRCA1",
    rsid: "rs80357906",
    finding_text: "BRCA1 c.5266dupC — Pathogenic variant in hereditary breast and ovarian cancer gene.",
    phenotype: "Hereditary breast and ovarian cancer syndrome",
    conditions: "Breast cancer",
    zygosity: "het",
    clinvar_significance: "Pathogenic",
    diplotype: null,
    metabolizer_status: null,
    drug: null,
    haplogroup: null,
    prs_score: null,
    prs_percentile: null,
    pathway: null,
    pathway_level: null,
    svg_path: null,
    pmid_citations: ["20301425"],
    detail: null,
    created_at: "2026-03-17T12:00:00",
  },
  {
    id: 2,
    module: "pharmacogenomics",
    category: "prescribing_alert",
    evidence_level: 4,
    gene_symbol: "CYP2C19",
    rsid: null,
    finding_text: "CYP2C19 *2/*2 — Poor Metabolizer. Clopidogrel may have reduced efficacy.",
    phenotype: null,
    conditions: null,
    zygosity: null,
    clinvar_significance: null,
    diplotype: "*2/*2",
    metabolizer_status: "Poor Metabolizer",
    drug: "clopidogrel",
    haplogroup: null,
    prs_score: null,
    prs_percentile: null,
    pathway: null,
    pathway_level: null,
    svg_path: null,
    pmid_citations: [],
    detail: null,
    created_at: "2026-03-17T12:00:00",
  },
  {
    id: 3,
    module: "nutrigenomics",
    category: "pathway",
    evidence_level: 3,
    gene_symbol: "MTHFR",
    rsid: "rs1801133",
    finding_text: "Folate metabolism — Elevated consideration. MTHFR C677T homozygous (TT).",
    phenotype: null,
    conditions: null,
    zygosity: "hom",
    clinvar_significance: null,
    diplotype: null,
    metabolizer_status: null,
    drug: null,
    haplogroup: null,
    prs_score: null,
    prs_percentile: null,
    pathway: "Folate Metabolism",
    pathway_level: "Elevated",
    svg_path: null,
    pmid_citations: ["15496427"],
    detail: null,
    created_at: "2026-03-17T12:00:00",
  },
  {
    id: 4,
    module: "ancestry",
    category: "composition",
    evidence_level: 2,
    gene_symbol: null,
    rsid: null,
    finding_text: "Primary ancestry: European (82%).",
    phenotype: null,
    conditions: null,
    zygosity: null,
    clinvar_significance: null,
    diplotype: null,
    metabolizer_status: null,
    drug: null,
    haplogroup: null,
    prs_score: null,
    prs_percentile: null,
    pathway: null,
    pathway_level: null,
    svg_path: null,
    pmid_citations: [],
    detail: null,
    created_at: "2026-03-17T12:00:00",
  },
]

const SAMPLE_SUMMARY: FindingsSummaryResponse = {
  total_findings: 4,
  modules: [
    { module: "cancer", count: 1, max_evidence_level: 4, top_finding_text: "BRCA1 c.5266dupC — Pathogenic" },
    { module: "pharmacogenomics", count: 1, max_evidence_level: 4, top_finding_text: "CYP2C19 *2/*2 — Poor Metabolizer" },
    { module: "nutrigenomics", count: 1, max_evidence_level: 3, top_finding_text: "Folate metabolism — Elevated consideration" },
    { module: "ancestry", count: 1, max_evidence_level: 2, top_finding_text: "Primary ancestry: European (82%)" },
  ],
  high_confidence_findings: SAMPLE_FINDINGS.slice(0, 3),
}

let mockFetch: ReturnType<typeof vi.fn>

beforeEach(() => {
  mockFetch = vi.fn()
  vi.stubGlobal("fetch", mockFetch)
})

function setupFetchMock(
  findings: Finding[] = SAMPLE_FINDINGS,
  summary: FindingsSummaryResponse = SAMPLE_SUMMARY,
) {
  mockFetch.mockImplementation(async (url: string) => {
    if (url.includes("/api/analysis/findings/summary")) {
      return { ok: true, json: async () => summary }
    }
    if (url.includes("/api/analysis/findings")) {
      return { ok: true, json: async () => findings }
    }
    return { ok: false, text: async () => "Not found" }
  })
}

// ── Tests ────────────────────────────────────────────────────────────

describe("FindingsExplorer", () => {
  it("shows no-sample state when no sample_id is provided", () => {
    renderWithRoute(<FindingsExplorer />)
    expect(screen.getByText("Select a sample to view analysis findings.")).toBeInTheDocument()
  })

  it("shows loading state while fetching findings", () => {
    mockFetch.mockReturnValue(new Promise(() => {}))
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])
    expect(screen.getByText("Loading findings...")).toBeInTheDocument()
  })

  it("renders all findings sorted by evidence level", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText(/BRCA1 c\.5266dupC/)).toBeInTheDocument()
    expect(screen.getByText(/CYP2C19 \*2\/\*2/)).toBeInTheDocument()
    expect(screen.getByText(/Folate metabolism/)).toBeInTheDocument()
    expect(screen.getByText(/Primary ancestry: European/)).toBeInTheDocument()
  })

  it("renders the zygosity label for findings that carry one", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])
    await screen.findByText(/BRCA1 c\.5266dupC/) // wait for findings to load

    // FindingsExplorer renders `finding.zygosity` for findings that have it
    // (the cancer finding is het, the nutrigenomics finding is hom). Assert both
    // labels render — a regression that dropped or inverted carriage rendering
    // would otherwise be invisible. (exact-text match, so "hom" does not collide
    // with the "homozygous (TT)" inside a finding_text.)
    expect(screen.getByText("het")).toBeInTheDocument()
    expect(screen.getByText("hom")).toBeInTheDocument()
  })

  it("displays module filter chips with counts", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    // Module names appear in both chips and finding rows; getAllByText confirms presence
    const cancerElements = await screen.findAllByText("Cancer")
    expect(cancerElements.length).toBeGreaterThanOrEqual(1)
    const pharmaElements = screen.getAllByText("Pharmacogenomics")
    expect(pharmaElements.length).toBeGreaterThanOrEqual(1)
    const nutriElements = screen.getAllByText("Nutrigenomics")
    expect(nutriElements.length).toBeGreaterThanOrEqual(1)
    const ancestryElements = screen.getAllByText("Ancestry")
    expect(ancestryElements.length).toBeGreaterThanOrEqual(1)
  })

  it("shows total findings count in header", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText("4 findings across 4 modules")).toBeInTheDocument()
  })

  it("shows empty state with no findings", async () => {
    setupFetchMock([], { total_findings: 0, modules: [], high_confidence_findings: [] })
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(
      await screen.findByText("No findings yet. Run annotation to generate analysis findings."),
    ).toBeInTheDocument()
  })

  it("displays evidence level group headings", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText("Definitive Evidence")).toBeInTheDocument()
    expect(screen.getByText("Strong Evidence")).toBeInTheDocument()
    expect(screen.getByText("Moderate Evidence")).toBeInTheDocument()
  })

  it("shows pathway level badge for nutrigenomics findings", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText("Elevated")).toBeInTheDocument()
  })

  it("shows ClinVar significance for cancer findings", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText("ClinVar: Pathogenic")).toBeInTheDocument()
  })

  it("shows error state when fetch fails", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      text: async () => "Server error",
    })
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText(/Findings failed: 500/)).toBeInTheDocument()
  })

  it("renders metabolizer status for pharmacogenomics findings", async () => {
    setupFetchMock()
    renderWithRoute(<FindingsExplorer />, ["/?sample_id=1"])

    expect(await screen.findByText("Poor Metabolizer")).toBeInTheDocument()
  })
})
