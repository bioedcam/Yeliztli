"""Tests for the resilient resumable HTTP download helper."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from backend.annotation.http_download import (
    _resolve_total,
    stream_download_with_resume,
)

# A deterministic payload large enough to exercise multi-chunk streaming.
PAYLOAD = bytes(range(256)) * 64  # 16 KiB

# Small streaming chunk so partial writes land on disk in tests. httpx's
# iter_bytes re-buffers to this size, so drop offsets are aligned to it to make
# the resumed byte offset deterministic.
TEST_CHUNK_SIZE = 1024


class _ScriptedTransport(httpx.BaseTransport):
    """A transport that replays a scripted sequence of response factories.

    Each factory receives the outgoing :class:`httpx.Request` and returns the
    :class:`httpx.Response` to serve for that attempt, letting tests model
    dropped connections, Range resumes, and Range-ignoring servers.
    """

    def __init__(self, factories: list) -> None:
        self._factories = list(factories)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        factory = self._factories.pop(0)
        return factory(request)


def _range_start(request: httpx.Request) -> int:
    """Parse the start byte from a ``Range: bytes=N-`` request header."""
    rng = request.headers.get("Range")
    if not rng:
        return 0
    return int(rng.removeprefix("bytes=").split("-", 1)[0])


def _full_ok(request: httpx.Request) -> httpx.Response:
    """A complete 200 response carrying the whole payload."""
    return httpx.Response(
        200,
        headers={"Content-Length": str(len(PAYLOAD))},
        content=PAYLOAD,
    )


def _dropped(at: int):
    """Factory for a 200 response that streams ``at`` bytes then disconnects."""

    def factory(request: httpx.Request) -> httpx.Response:
        def body():
            yield PAYLOAD[:at]
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body",
                request=request,
            )

        return httpx.Response(
            200,
            headers={"Content-Length": str(len(PAYLOAD))},
            content=body(),
        )

    return factory


def _resume(request: httpx.Request) -> httpx.Response:
    """A 206 partial response honouring the request's Range header."""
    start = _range_start(request)
    body = PAYLOAD[start:]
    return httpx.Response(
        206,
        headers={
            "Content-Range": f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}",
            "Content-Length": str(len(body)),
        },
        content=body,
    )


def _download(tmp_path: Path, transport: httpx.BaseTransport, **kwargs) -> Path:
    return stream_download_with_resume(
        "https://example.invalid/file.bin",
        tmp_path / "file.bin",
        transport=transport,
        backoff_base=0.0,  # no sleeps in tests
        chunk_size=TEST_CHUNK_SIZE,
        **kwargs,
    )


class TestStreamDownloadWithResume:
    def test_successful_single_attempt(self, tmp_path: Path):
        transport = _ScriptedTransport([_full_ok])
        dest = _download(tmp_path, transport)

        assert dest.read_bytes() == PAYLOAD
        assert len(transport.requests) == 1
        # No leftover temp file.
        assert not (tmp_path / "file.bin.tmp").exists()

    def test_resumes_after_dropped_connection(self, tmp_path: Path):
        # First attempt drops half-way; second resumes via Range.
        transport = _ScriptedTransport([_dropped(at=len(PAYLOAD) // 2), _resume])
        dest = _download(tmp_path, transport)

        assert dest.read_bytes() == PAYLOAD
        assert len(transport.requests) == 2
        # The retry must carry a Range header starting at the dropped offset.
        assert transport.requests[1].headers["Range"] == f"bytes={len(PAYLOAD) // 2}-"

    def test_multiple_drops_then_success(self, tmp_path: Path):
        transport = _ScriptedTransport([_dropped(at=1000), _dropped(at=4000), _resume])
        dest = _download(tmp_path, transport)

        assert dest.read_bytes() == PAYLOAD
        assert len(transport.requests) == 3

    def test_exhausts_retries_and_raises(self, tmp_path: Path):
        # Every attempt drops; with max_retries=2 we get 3 attempts total.
        transport = _ScriptedTransport([_dropped(at=10)] * 3)
        with pytest.raises(httpx.RemoteProtocolError):
            _download(tmp_path, transport, max_retries=2)

        assert len(transport.requests) == 3
        # Partial file is preserved for a future resume.
        assert (tmp_path / "file.bin.tmp").exists()

    def test_resumes_from_existing_partial_file(self, tmp_path: Path):
        # Simulate a partial left by a previous invocation.
        partial = tmp_path / "file.bin.tmp"
        partial.write_bytes(PAYLOAD[:5000])

        transport = _ScriptedTransport([_resume])
        dest = _download(tmp_path, transport)

        assert dest.read_bytes() == PAYLOAD
        assert transport.requests[0].headers["Range"] == "bytes=5000-"

    def test_server_ignores_range_restarts_from_scratch(self, tmp_path: Path):
        # Pre-existing partial, but the server replies 200 (ignores Range).
        partial = tmp_path / "file.bin.tmp"
        partial.write_bytes(b"STALE-PARTIAL-DATA")

        transport = _ScriptedTransport([_full_ok])
        dest = _download(tmp_path, transport)

        # Restarted cleanly: result is exactly the payload, not payload+stale.
        assert dest.read_bytes() == PAYLOAD

    def test_truncated_stream_without_error_is_retried(self, tmp_path: Path):
        # Server claims full length but quietly delivers fewer bytes and ends
        # the stream without raising — must be detected as incomplete.
        def short_body(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Length": str(len(PAYLOAD))},
                content=PAYLOAD[: len(PAYLOAD) // 2],
            )

        transport = _ScriptedTransport([short_body, _resume])
        dest = _download(tmp_path, transport)

        assert dest.read_bytes() == PAYLOAD
        assert len(transport.requests) == 2

    def test_range_not_satisfiable_restarts(self, tmp_path: Path):
        # Partial is stale/oversized; server returns 416, then a clean 200.
        partial = tmp_path / "file.bin.tmp"
        partial.write_bytes(PAYLOAD + b"EXTRA")

        def not_satisfiable(request: httpx.Request) -> httpx.Response:
            return httpx.Response(416, request=request)

        transport = _ScriptedTransport([not_satisfiable, _full_ok])
        dest = _download(tmp_path, transport)

        assert dest.read_bytes() == PAYLOAD

    def test_non_retryable_status_raises(self, tmp_path: Path):
        def not_found(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        transport = _ScriptedTransport([not_found])
        with pytest.raises(httpx.HTTPStatusError):
            _download(tmp_path, transport)

    def test_progress_callback_reports_total(self, tmp_path: Path):
        seen: list[tuple[int, int | None]] = []
        transport = _ScriptedTransport([_full_ok])
        _download(tmp_path, transport, progress_callback=lambda d, t: seen.append((d, t)))

        assert seen, "progress callback was never invoked"
        # Final reported offset equals the full payload size.
        assert seen[-1][0] == len(PAYLOAD)
        assert all(total == len(PAYLOAD) for _, total in seen)

    def test_progress_is_cumulative_across_resume(self, tmp_path: Path):
        seen: list[int] = []
        half = len(PAYLOAD) // 2
        transport = _ScriptedTransport([_dropped(at=half), _resume])
        _download(tmp_path, transport, progress_callback=lambda d, t: seen.append(d))

        # Offsets are monotonically non-decreasing and reach the full size.
        assert seen == sorted(seen)
        assert seen[-1] == len(PAYLOAD)


class TestResolveTotal:
    def test_206_uses_content_range_total(self):
        resp = httpx.Response(
            206, headers={"Content-Range": "bytes 100-199/5000", "Content-Length": "100"}
        )
        assert _resolve_total(resp, offset=100) == 5000

    def test_206_falls_back_to_offset_plus_content_length(self):
        resp = httpx.Response(206, headers={"Content-Length": "900"})
        assert _resolve_total(resp, offset=100) == 1000

    def test_206_star_total_falls_back(self):
        resp = httpx.Response(
            206, headers={"Content-Range": "bytes 100-199/*", "Content-Length": "900"}
        )
        assert _resolve_total(resp, offset=100) == 1000

    def test_200_uses_content_length(self):
        resp = httpx.Response(200, headers={"Content-Length": "4242"})
        assert _resolve_total(resp, offset=0) == 4242

    def test_missing_length_returns_none(self):
        resp = httpx.Response(200)
        assert _resolve_total(resp, offset=0) is None
