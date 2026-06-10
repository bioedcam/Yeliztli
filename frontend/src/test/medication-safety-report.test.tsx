/** Tests for the consolidated medication-safety report (SW-E4). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "./test-utils"
import MedicationSafetyReport from "@/components/pharmacogenomics/MedicationSafetyReport"
import type { MedicationSafetyReportResponse } from "@/types/pharmacogenomics"

// Mock the report API hook so the component is driven by fixture data.
const mockUseReport = vi.fn()
vi.mock("@/api/pharmacogenomics", () => ({
  usePharmaReport: (...args: unknown[]) => mockUseReport(...args),
}))

const DISCLOSURE =
  "About this medication-safety report (context only). Phenotype terms follow the CPIC " +
  "consensus standard. Reference-biased: a Normal Metabolizer result does NOT rule out " +
  "untested or copy-number alleles."

const REPORT: MedicationSafetyReportResponse = {
  reference_bias_disclosure: DISCLOSURE,
  genes_assessed: 3,
  drugs_assessed: 3,
  actionable_drug_count: 2,
  gene_coverage: [
    {
      gene: "CYP2C19",
      diplotype: "*1/*2",
      phenotype: "Intermediate Metabolizer",
      call_confidence: "Complete",
      confidence_note: null,
      coverage: { assessed: 3, total: 4 },
      activity_score: 0.5,
      ehr_notation: "Intermediate Metabolizer",
      evidence_level: 4,
      gene_caveat: null,
    },
    {
      gene: "CYP2D6",
      diplotype: "*1/*1",
      phenotype: "Normal Metabolizer",
      call_confidence: "Partial",
      confidence_note: "Structural variant gene.",
      coverage: { assessed: 5, total: 5 },
      activity_score: 2.0,
      ehr_notation: "Normal Metabolizer",
      evidence_level: 4,
      gene_caveat: "CYP2D6 copy-number caveat.",
    },
    {
      gene: "DPYD",
      diplotype: "*1/*2A",
      phenotype: "Intermediate Metabolizer",
      call_confidence: "Complete",
      confidence_note: null,
      coverage: { assessed: 4, total: 4 },
      activity_score: 1.0,
      ehr_notation: "DPYD Intermediate Metabolizer",
      evidence_level: 4,
      gene_caveat: "DPYD context: does NOT rule out fatal fluoropyrimidine toxicity.",
    },
  ],
  drugs: [
    {
      drug: "clopidogrel",
      actionable: true,
      gene_effects: [
        {
          gene: "CYP2C19",
          diplotype: "*1/*2",
          phenotype: "Intermediate Metabolizer",
          recommendation: "Consider alternative antiplatelet therapy.",
          classification: "A",
          guideline_url: "https://cpicpgx.org/",
          call_confidence: "Complete",
          confidence_note: null,
          evidence_level: 4,
          activity_score: 0.5,
          ehr_notation: "Intermediate Metabolizer",
          coverage: { assessed: 3, total: 4 },
          actionability: "actionable",
          gene_caveat: null,
        },
      ],
    },
    {
      drug: "fluorouracil",
      actionable: true,
      gene_effects: [
        {
          gene: "DPYD",
          diplotype: "*1/*2A",
          phenotype: "Intermediate Metabolizer",
          recommendation: "Reduce starting dose by 50%, then titrate.",
          classification: "A",
          guideline_url: "https://cpicpgx.org/",
          call_confidence: "Complete",
          confidence_note: null,
          evidence_level: 4,
          activity_score: 1.0,
          ehr_notation: "DPYD Intermediate Metabolizer",
          coverage: { assessed: 4, total: 4 },
          actionability: "actionable",
          gene_caveat: "DPYD context: does NOT rule out fatal fluoropyrimidine toxicity.",
        },
      ],
    },
    {
      drug: "codeine",
      actionable: false,
      gene_effects: [
        {
          gene: "CYP2D6",
          diplotype: "*1/*1",
          phenotype: "Normal Metabolizer",
          recommendation: "Use label-recommended dosing.",
          classification: "A",
          guideline_url: "https://cpicpgx.org/",
          call_confidence: "Partial",
          confidence_note: "Structural variant gene.",
          evidence_level: 4,
          activity_score: 2.0,
          ehr_notation: "Normal Metabolizer",
          coverage: { assessed: 5, total: 5 },
          actionability: "routine",
          gene_caveat: "CYP2D6 copy-number caveat.",
        },
      ],
    },
  ],
}

describe("MedicationSafetyReport", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders nothing while loading", () => {
    mockUseReport.mockReturnValue({ data: undefined, isLoading: true, isError: false })
    const { container } = render(<MedicationSafetyReport sampleId={1} />)
    expect(container.innerHTML).toBe("")
  })

  it("renders nothing on error", () => {
    mockUseReport.mockReturnValue({ data: undefined, isLoading: false, isError: true })
    const { container } = render(<MedicationSafetyReport sampleId={1} />)
    expect(container.innerHTML).toBe("")
  })

  it("renders nothing when no genes were assessed", () => {
    mockUseReport.mockReturnValue({
      data: { ...REPORT, genes_assessed: 0, drugs: [], gene_coverage: [] },
      isLoading: false,
      isError: false,
    })
    const { container } = render(<MedicationSafetyReport sampleId={1} />)
    expect(container.innerHTML).toBe("")
  })

  it("renders the reference-bias disclosure verbatim", () => {
    mockUseReport.mockReturnValue({ data: REPORT, isLoading: false, isError: false })
    render(<MedicationSafetyReport sampleId={1} />)
    const note = screen.getByRole("note", { name: "About this medication-safety report" })
    expect(note).toHaveTextContent(/CPIC consensus standard/)
    expect(note).toHaveTextContent(/does NOT rule out/)
  })

  it("renders the summary counts", () => {
    mockUseReport.mockReturnValue({ data: REPORT, isLoading: false, isError: false })
    render(<MedicationSafetyReport sampleId={1} />)
    expect(screen.getByText(/2 flagged for review/)).toBeInTheDocument()
  })

  it("renders a card per drug with CPIC phenotype term and recommendation", () => {
    mockUseReport.mockReturnValue({ data: REPORT, isLoading: false, isError: false })
    render(<MedicationSafetyReport sampleId={1} />)
    const clopidogrel = screen.getByRole("article", { name: "clopidogrel medication safety" })
    expect(within(clopidogrel).getByText("Intermediate Metabolizer")).toBeInTheDocument()
    expect(
      within(clopidogrel).getByText(/Consider alternative antiplatelet therapy/),
    ).toBeInTheDocument()
  })

  it("flags actionable drugs and labels routine ones", () => {
    mockUseReport.mockReturnValue({ data: REPORT, isLoading: false, isError: false })
    render(<MedicationSafetyReport sampleId={1} />)
    const clopidogrel = screen.getByRole("article", { name: "clopidogrel medication safety" })
    expect(within(clopidogrel).getByText("Review recommended")).toBeInTheDocument()
    const codeine = screen.getByRole("article", { name: "codeine medication safety" })
    expect(within(codeine).getByText("Routine")).toBeInTheDocument()
  })

  it("shows SNP coverage per gene effect", () => {
    mockUseReport.mockReturnValue({ data: REPORT, isLoading: false, isError: false })
    render(<MedicationSafetyReport sampleId={1} />)
    const clopidogrel = screen.getByRole("article", { name: "clopidogrel medication safety" })
    expect(within(clopidogrel).getByText("3/4 positions")).toBeInTheDocument()
  })

  it("surfaces gene-specific caveats as notes", () => {
    mockUseReport.mockReturnValue({ data: REPORT, isLoading: false, isError: false })
    render(<MedicationSafetyReport sampleId={1} />)
    const note = screen.getByRole("note", { name: "DPYD interpretation caveat" })
    expect(note).toHaveTextContent(/fatal fluoropyrimidine toxicity/)
  })

  it("renders a per-gene coverage panel from gene_coverage", () => {
    mockUseReport.mockReturnValue({ data: REPORT, isLoading: false, isError: false })
    render(<MedicationSafetyReport sampleId={1} />)
    const panel = screen.getByRole("region", { name: "Per-gene coverage and confidence" })
    // Every assessed gene is listed in the consolidated panel.
    expect(within(panel).getByText("CYP2C19")).toBeInTheDocument()
    expect(within(panel).getByText("CYP2D6")).toBeInTheDocument()
    expect(within(panel).getByText("DPYD")).toBeInTheDocument()
    // SNP coverage shown per gene.
    expect(within(panel).getByText("3/4 positions")).toBeInTheDocument()
  })

  it("marks genes carrying an interpretation caveat in the coverage panel", () => {
    mockUseReport.mockReturnValue({ data: REPORT, isLoading: false, isError: false })
    render(<MedicationSafetyReport sampleId={1} />)
    const panel = screen.getByRole("region", { name: "Per-gene coverage and confidence" })
    expect(
      within(panel).getByLabelText("CYP2D6 has an interpretation caveat"),
    ).toBeInTheDocument()
  })

  it("renders drugs in the order provided (actionable first)", () => {
    mockUseReport.mockReturnValue({ data: REPORT, isLoading: false, isError: false })
    render(<MedicationSafetyReport sampleId={1} />)
    const articles = screen.getAllByRole("article")
    const names = articles.map((a) => a.getAttribute("aria-label"))
    expect(names).toEqual([
      "clopidogrel medication safety",
      "fluorouracil medication safety",
      "codeine medication safety",
    ])
  })
})
