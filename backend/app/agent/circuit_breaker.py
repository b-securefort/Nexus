"""
Thread-safe circuit breaker for Azure OpenAI calls.

States:
  closed   — normal operation; calls pass through.
  open     — failure threshold exceeded; calls are rejected immediately.
  half_open — probe state once OPEN_SECONDS have elapsed; one attempt is
              allowed through. A success closes the circuit; a failure
              resets the open timer.

Configuration via Settings (all have safe defaults):
  AOAI_CB_FAILURE_THRESHOLD  — consecutive failures within the window before
                               opening (default 5).
  AOAI_CB_WINDOW_SECONDS     — rolling window size in seconds (default 60).
  AOAI_CB_OPEN_SECONDS       — how long to stay open before transitioning to
                               half-open for a probe (default 30).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Literal

from app.config import get_settings

logger = logging.getLogger(__name__)

State = Literal["closed", "open", "half_open"]

_lock = threading.Lock()
_failure_count: int = 0
_window_start: float = 0.0
_open_until: float = 0.0


# ── State inspection ─────────────────────────────────────────────────────────


def get_state() -> State:
    """Return the current circuit state without mutating anything."""
    with _lock:
        now = time.monotonic()
        if _open_until > now:
            return "open"
        if _open_until > 0.0:
            # Grace period just expired — allow one probe
            return "half_open"
        return "closed"


def get_status_dict() -> dict:
    """Return a JSON-serialisable summary for health endpoints."""
    with _lock:
        now = time.monotonic()
        state: State
        if _open_until > now:
            state = "open"
        elif _open_until > 0.0:
            state = "half_open"
        else:
            state = "closed"
        return {
            "state": state,
            "failure_count": _failure_count,
            "seconds_until_probe": max(0, round(_open_until - now, 1)) if state == "open" else 0,
        }


# ── Outcome recording ─────────────────────────────────────────────────────────


def record_success() -> None:
    """A completions call succeeded — reset failure state and close circuit."""
    global _failure_count, _window_start, _open_until
    with _lock:
        if _open_until > 0.0:
            logger.info("circuit_breaker: probe succeeded — closing circuit")
        _failure_count = 0
        _window_start = 0.0
        _open_until = 0.0


def record_failure() -> None:
    """A completions call failed — increment counter; open circuit if threshold hit."""
    global _failure_count, _window_start, _open_until
    with _lock:
        settings = get_settings()
        now = time.monotonic()
        window = float(settings.AOAI_CB_WINDOW_SECONDS)

        # Reset window counter if the window has rolled over
        if _window_start == 0.0 or (now - _window_start) > window:
            _window_start = now
            _failure_count = 0

        _failure_count += 1
        logger.warning(
            "circuit_breaker: failure %d/%d within %ds window",
            _failure_count,
            settings.AOAI_CB_FAILURE_THRESHOLD,
            settings.AOAI_CB_WINDOW_SECONDS,
        )

        if _failure_count >= settings.AOAI_CB_FAILURE_THRESHOLD:
            _open_until = now + float(settings.AOAI_CB_OPEN_SECONDS)
            logger.error(
                "circuit_breaker: OPEN — %d failures in window; "
                "rejecting calls for %ds",
                _failure_count,
                settings.AOAI_CB_OPEN_SECONDS,
            )


# ── Guard ─────────────────────────────────────────────────────────────────────


class CircuitOpenError(Exception):
    """Raised when an Azure OpenAI call is attempted while the circuit is open."""


def check() -> None:
    """Raise CircuitOpenError if the circuit is currently open.

    Call this before every Azure OpenAI completions/embeddings request so that
    callers get a fast, informative failure instead of hanging for the full SDK
    timeout and then incrementing the failure counter a second time.
    """
    if get_state() == "open":
        raise CircuitOpenError(
            "Azure OpenAI is temporarily unavailable (circuit breaker open). "
            "Please wait a moment and try again."
        )


def reset() -> None:
    """Reset all circuit breaker state to closed.

    Intended for use in tests only — resets the module-level counters so
    tests that exercise the 'failing client' path don't contaminate later tests.
    """
    global _failure_count, _window_start, _open_until
    with _lock:
        _failure_count = 0
        _window_start = 0.0
        _open_until = 0.0
