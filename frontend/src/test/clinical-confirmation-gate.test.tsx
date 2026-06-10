import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import ClinicalConfirmationGate from "@/components/ui/ClinicalConfirmationGate"

describe("ClinicalConfirmationGate", () => {
  it("renders the confirm-in-CLIA framing", () => {
    render(<ClinicalConfirmationGate />)
    const gate = screen.getByTestId("clia-confirmation-gate")
    expect(gate).toBeInTheDocument()
    expect(screen.getByText(/Confirm before acting — not a clinical diagnosis/)).toBeInTheDocument()
    expect(screen.getByText(/CLIA\/accredited laboratory/)).toBeInTheDocument()
    expect(screen.getByText(/genetic counselor or clinician/)).toBeInTheDocument()
  })

  it("is non-dismissible (no close control)", () => {
    render(<ClinicalConfirmationGate />)
    // A dismissible banner would expose a button/close affordance; this gate must not.
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })
})
