"""Resilient streaming HTTP download with retry + HTTP Range resume.

The large reference databases (dbNSFP ~48 GB, gnomAD ~60 GB) are fetched as a
single HTTP stream.  A dropped connection part-way through used to discard all
progress and fail the whole build with a ``RemoteProtocolError`` such as::

    peer closed connection without sending complete message body
    (received 5489295360 bytes, expected 47813707544)

This helper makes those downloads survivable: on a transient transport error it
retries with exponential backoff, resuming from the last byte already written
via an HTTP ``Range`` request, and it verifies the final file size against the
server-advertised total so a silently truncated stream is treated as a failure
rather than a success.

The partial ``.tmp`` file is preserved between attempts (and between separate
calls) so a re-triggered download picks up where the previous one left off
instead of restarting from zero.

Usage::

    from backend.annotation.http_download import stream_download_with_resume

    stream_download_with_resume(url, dest_path, progress_callback=cb)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = structlog.get_logger(__name__)

# Default streaming read size.
CHUNK_SIZE = 65_536  # 64 KiB

# Transport-level failures that indicate a transient, retryable condition.
# Notably includes RemoteProtocolError, the error raised when the peer closes
# the connection before the full body is delivered.
_TRANSIENT_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.WriteError,
    httpx.PoolTimeout,
)


def stream_download_with_resume(
    url: str,
    dest_path: Path,
    *,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 3600.0,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    chunk_size: int = CHUNK_SIZE,
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """Stream ``url`` to ``dest_path``, retrying and resuming on dropped connections.

    Data is streamed to ``<dest_path>.tmp`` and atomically renamed to
    ``dest_path`` once the full body has been received.  If a transient network
    error interrupts the transfer, the download is retried (up to
    ``max_retries`` times) with an HTTP ``Range`` header so it resumes from the
    bytes already on disk.  Any pre-existing ``.tmp`` file is treated as a
    resumable partial.

    Args:
        url: Remote URL to download.
        dest_path: Final destination path for the completed file.
        progress_callback: Called with ``(bytes_downloaded, total_bytes)``;
            ``total_bytes`` is ``None`` when the server does not advertise a size.
        timeout: Total HTTP timeout in seconds.
        max_retries: Maximum number of retry attempts after the first try.
        backoff_base: Base seconds for exponential backoff between retries
            (sleep = ``backoff_base * 2 ** (attempt - 1)``); ``0`` disables it.
        chunk_size: Streaming read size in bytes.
        transport: Optional httpx transport override (used in tests).

    Returns:
        ``dest_path`` on success.

    Raises:
        httpx.HTTPError: If the download fails after exhausting all retries, or
            on a non-retryable HTTP error (e.g. 404).
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_name(dest_path.name + ".tmp")

    # Resume from any partial file left by a previous attempt or invocation.
    offset = tmp_path.stat().st_size if tmp_path.exists() else 0
    total_bytes: int | None = None
    attempt = 0

    while True:
        headers: dict[str, str] = {}
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"

        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=httpx.Timeout(timeout, connect=30.0, read=120.0),
                transport=transport,
            ) as client:
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code == 416:
                        # Range Not Satisfiable: our partial is at/past the
                        # server's size. Discard it and restart cleanly.
                        logger.warning(
                            "download_range_not_satisfiable_restart",
                            url=url,
                            offset=offset,
                        )
                        tmp_path.unlink(missing_ok=True)
                        offset = 0
                        raise httpx.RemoteProtocolError(
                            "range not satisfiable; restarting",
                            request=response.request,
                        )

                    if offset > 0 and response.status_code == 200:
                        # Server ignored the Range header and is sending the
                        # whole file — discard the partial and start over.
                        logger.info("download_range_unsupported_restart", url=url)
                        offset = 0

                    response.raise_for_status()
                    total_bytes = _resolve_total(response, offset)

                    mode = "ab" if offset > 0 else "wb"
                    with open(tmp_path, mode) as f:
                        for chunk in response.iter_bytes(chunk_size=chunk_size):
                            f.write(chunk)
                            offset += len(chunk)
                            if progress_callback:
                                progress_callback(offset, total_bytes)

            # Stream finished without raising — guard against silent truncation.
            if total_bytes is not None and offset < total_bytes:
                raise httpx.RemoteProtocolError(
                    f"incomplete download: received {offset} of {total_bytes} bytes",
                )
            break  # success

        except _TRANSIENT_ERRORS as exc:
            attempt += 1
            # Re-sync the offset to the bytes actually flushed to disk; a partial
            # final chunk write can leave the counter ahead of the file.
            offset = tmp_path.stat().st_size if tmp_path.exists() else 0
            if attempt > max_retries:
                logger.error(
                    "download_retries_exhausted",
                    url=url,
                    attempts=attempt,
                    offset=offset,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
            sleep_s = backoff_base * (2 ** (attempt - 1))
            logger.warning(
                "download_retry",
                url=url,
                attempt=attempt,
                max_retries=max_retries,
                offset=offset,
                sleep_s=sleep_s,
                error=f"{type(exc).__name__}: {exc}",
            )
            if sleep_s > 0:
                time.sleep(sleep_s)

    tmp_path.replace(dest_path)
    return dest_path


def _resolve_total(response: httpx.Response, offset: int) -> int | None:
    """Resolve the full expected file size in bytes, or ``None`` if unknown.

    For a 206 partial response the total comes from the ``Content-Range``
    header (``bytes START-END/TOTAL``); if absent, ``Content-Length`` is the
    *remaining* bytes and must be added to the current offset.  For a 200
    response ``Content-Length`` is the full size.
    """
    if response.status_code == 206:
        content_range = response.headers.get("Content-Range", "")
        if "/" in content_range:
            total_str = content_range.rsplit("/", 1)[1].strip()
            if total_str != "*":
                try:
                    return int(total_str)
                except ValueError:
                    pass
        content_length = response.headers.get("Content-Length")
        return offset + int(content_length) if content_length else None

    content_length = response.headers.get("Content-Length")
    return int(content_length) if content_length else None
