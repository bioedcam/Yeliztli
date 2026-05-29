/** PostMergeRewatchModal unit tests (Step 72 / MRG-13; Plan §10.6, §10.7).
 *
 * Covers:
 *   - SSE gate: the migrate-from-sources fetch is deferred until the
 *     merged sample's annotation cascade reports `status='complete'`.
 *   - Per-row mutation: clicking "Re-watch" fires `POST /api/watches`
 *     with `{sample_id: merged_sample_id, rsid: rsid_on_merged, notes}`.
 *   - "Re-watch all" batches the per-row mutations.
 *   - Private-rsid rows (where `rsid_on_merged_or_null` is `null`) are
 *     listed for transparency but the per-row button is disabled.
 *   - 423 from the migrate endpoint (race between SSE and the
 *     `annotation_state` upsert) renders the benign "still annotating"
 *     banner, NOT an error.
 *   - Empty candidates list renders the "nothing to migrate" branch.
 *
 * Step 87 (MRG-12) is the canonical Phase 3 coverage gate; this file
 * lands the test surface in the same PR as the implementation per
 * CLAUDE.md DoD.
 */

import { act } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "./test-utils"

import { PostMergeRewatchModal } from "@/components/individuals/PostMergeRewatchModal"

// ── Mock fetch + EventSource ─────────────────────────────────────────

const mockFetch = vi.fn()

type EventSourceListener = (event: MessageEvent) => void

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  listeners: Record<string, EventSourceListener[]> = {}
  readyState = 0

  constructor(url: string) {
    this.url = url
    this.readyState = 1
    MockEventSource.instances.push(this)
  }

  addEventListener(event: string, listener: EventSourceListener) {
    if (!this.listeners[event]) this.listeners[event] = []
    this.listeners[event].push(listener)
  }

  close() {
    this.readyState = 2
  }

  _emit(event: string, data: unknown) {
    const listeners = this.listeners[event] ?? []
    for (const fn of listeners) {
      fn(new MessageEvent(event, { data: JSON.stringify(data) }))
    }
  }
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    clone() {
      return this
    },
  } as unknown as Response
}

const CANDIDATES_PAYLOAD = {
  candidates: [
    {
      rsid_on_source: "rs300_old",
      notes_on_source: "rsid-collapse note",
      sample_id: 11,
      chrom: "1",
      pos: 300,
      rsid_on_merged_or_null: "rs300_new",
    },
    {
      rsid_on_source: "rs999_s1",
      notes_on_source: "private to S1",
      sample_id: 11,
      chrom: "2",
      pos: 900,
      rsid_on_merged_or_null: null,
    },
  ],
}

function renderModal(overrides: { jobId?: string | null; onClose?: () => void } = {}) {
  // ``?? "merge-job-1"`` would coerce an explicit ``null`` back to the
  // default — use ``in``/``hasOwnProperty`` to preserve the caller's
  // explicit null so the "no job scheduled" branch can be exercised.
  const jobId = "jobId" in overrides ? overrides.jobId! : "merge-job-1"
  return render(
    <PostMergeRewatchModal
      mergedSampleId={99}
      jobId={jobId}
      onClose={overrides.onClose ?? (() => {})}
    />,
  )
}

function emitAnnotationComplete() {
  // SSE listeners attach in a useEffect — wait one microtask before
  // emitting so the EventSource exists.
  return waitFor(() => {
    expect(MockEventSource.instances).toHaveLength(1)
  }).then(() =>
    act(() => {
      MockEventSource.instances[0]._emit("progress", {
        job_id: "merge-job-1",
        status: "complete",
        progress_pct: 100,
        message: "done",
        error: null,
      })
    }),
  )
}

beforeEach(() => {
  mockFetch.mockReset()
  MockEventSource.instances = []
  vi.stubGlobal("fetch", mockFetch)
  vi.stubGlobal("EventSource", MockEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// ═══════════════════════════════════════════════════════════════════════

describe("PostMergeRewatchModal — SSE gating", () => {
  it("defers the migrate fetch until annotation completes", async () => {
    mockFetch.mockResolvedValue(jsonResponse(CANDIDATES_PAYLOAD))

    renderModal()

    // Pre-completion: no fetch yet, "waiting for annotation" branch.
    expect(
      screen.getByTestId("rewatch-modal-annotating"),
    ).toBeInTheDocument()
    expect(mockFetch).not.toHaveBeenCalled()

    await emitAnnotationComplete()

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/samples/99/watched-variants/migrate-from-sources",
      )
    })
  })

  it("renders 'no job scheduled' branch when jobId is null", () => {
    renderModal({ jobId: null })
    expect(screen.getByTestId("rewatch-modal-no-job")).toBeInTheDocument()
    expect(mockFetch).not.toHaveBeenCalled()
  })

  it("renders the benign 'still annotating' banner when the route 423s post-complete", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ detail: { error: "sample_annotation_stale" } }, 423),
    )

    renderModal()
    await emitAnnotationComplete()

    await waitFor(() => {
      expect(
        screen.getByTestId("rewatch-modal-stale-banner"),
      ).toBeInTheDocument()
    })
  })
})

// ═══════════════════════════════════════════════════════════════════════

describe("PostMergeRewatchModal — candidates table", () => {
  it("renders rows for each candidate and disables the button on private rsids", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(CANDIDATES_PAYLOAD))

    renderModal()
    await emitAnnotationComplete()

    await waitFor(() => {
      expect(
        screen.getByTestId("rewatch-modal-candidate-table"),
      ).toBeInTheDocument()
    })

    const collapseButton = screen.getByTestId(
      "rewatch-row-11:rs300_old-button",
    ) as HTMLButtonElement
    const privateButton = screen.getByTestId(
      "rewatch-row-11:rs999_s1-button",
    ) as HTMLButtonElement
    expect(collapseButton.disabled).toBe(false)
    expect(privateButton.disabled).toBe(true)
  })

  it("re-watches a single row via POST /api/watches with the merged rsid + source notes", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(CANDIDATES_PAYLOAD))
    mockFetch.mockResolvedValueOnce(jsonResponse({}, 201))

    renderModal()
    await emitAnnotationComplete()

    await waitFor(() =>
      expect(
        screen.getByTestId("rewatch-modal-candidate-table"),
      ).toBeInTheDocument(),
    )

    fireEvent.click(
      screen.getByTestId("rewatch-row-11:rs300_old-button"),
    )

    await waitFor(() => {
      expect(
        screen.getByTestId("rewatch-row-11:rs300_old-success"),
      ).toBeInTheDocument()
    })

    const postCall = mockFetch.mock.calls.find(
      (call) => call[0] === "/api/watches",
    )
    expect(postCall).toBeTruthy()
    expect(postCall![1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        sample_id: 99,
        rsid: "rs300_new",
        notes: "rsid-collapse note",
      }),
    })
  })

  it("'Re-watch all' batches per-row mutations across rewatchable rows only", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(CANDIDATES_PAYLOAD))
    mockFetch.mockResolvedValueOnce(jsonResponse({}, 201))

    renderModal()
    await emitAnnotationComplete()

    await waitFor(() =>
      expect(
        screen.getByTestId("rewatch-modal-rewatch-all"),
      ).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByTestId("rewatch-modal-rewatch-all"))

    await waitFor(() => {
      expect(
        screen.getByTestId("rewatch-row-11:rs300_old-success"),
      ).toBeInTheDocument()
    })

    // Only one /api/watches call — the private row is skipped.
    const postCalls = mockFetch.mock.calls.filter(
      (call) => call[0] === "/api/watches",
    )
    expect(postCalls).toHaveLength(1)
  })

  it("renders the empty-state branch when no candidates exist", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ candidates: [] }))

    renderModal()
    await emitAnnotationComplete()

    await waitFor(() =>
      expect(screen.getByTestId("rewatch-modal-empty")).toBeInTheDocument(),
    )
  })

  it("surfaces a per-row error message when /api/watches fails", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(CANDIDATES_PAYLOAD))
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ detail: "Internal error" }, 500),
    )

    renderModal()
    await emitAnnotationComplete()

    await waitFor(() =>
      expect(
        screen.getByTestId("rewatch-modal-candidate-table"),
      ).toBeInTheDocument(),
    )

    fireEvent.click(
      screen.getByTestId("rewatch-row-11:rs300_old-button"),
    )

    await waitFor(() =>
      expect(
        screen.getByTestId("rewatch-row-11:rs300_old-error"),
      ).toBeInTheDocument(),
    )
  })

  it("treats HTTP 409 (already watched) as an idempotent success", async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(CANDIDATES_PAYLOAD))
    mockFetch.mockResolvedValueOnce(
      jsonResponse({ detail: "Already watched" }, 409),
    )

    renderModal()
    await emitAnnotationComplete()

    await waitFor(() =>
      expect(
        screen.getByTestId("rewatch-modal-candidate-table"),
      ).toBeInTheDocument(),
    )

    fireEvent.click(
      screen.getByTestId("rewatch-row-11:rs300_old-button"),
    )

    await waitFor(() =>
      expect(
        screen.getByTestId("rewatch-row-11:rs300_old-success"),
      ).toBeInTheDocument(),
    )
  })
})

// ═══════════════════════════════════════════════════════════════════════

describe("PostMergeRewatchModal — dismissibility", () => {
  it("fires onClose when Dismiss is clicked", () => {
    const onClose = vi.fn()
    renderModal({ onClose })
    fireEvent.click(screen.getByTestId("rewatch-modal-dismiss"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("fires onClose when the X is clicked", () => {
    const onClose = vi.fn()
    renderModal({ onClose })
    fireEvent.click(screen.getByTestId("rewatch-modal-close"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
