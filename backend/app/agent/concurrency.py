"""
Concurrency primitives for orchestrator tool dispatch.

Provides:
  - `tool_executor()` — dedicated ThreadPoolExecutor for tool work, so tool
    subprocesses can't starve unrelated workloads (SQLite, KB indexing) that
    share Python's default executor.
  - `get_user_semaphore(user_oid)` — bounded per-user concurrency. Caps one
    user's in-flight tool calls so a single user firing many parallel
    queries can't exhaust the executor and freeze other users out.
  - `run_in_tool_executor(fn, *args)` — convenience wrapper that runs the
    callable on the dedicated executor with `copy_context()` so
    `ContextVar`s (ARM token, active skill) survive the hop.

Designed to be drop-in compatible with `asyncio.to_thread` for callers that
just want a thread off the event loop; the value-add is the bounded pool +
per-user semaphore. Leaves OpenAI/SQLite/GitPython/MSAL on the default
threads — only orchestrator tool dispatch is routed through here.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Cap the executor pool well below the SQLite connection pool / OS thread
# ceiling — 64 is enough headroom for ~16 simultaneously-chatting users at
# the per-user max of 4 below.
_TOOL_EXECUTOR_MAX_WORKERS = 64
_DEFAULT_USER_MAX_CONCURRENT = 4

_executor_lock = threading.Lock()
_executor: Optional[ThreadPoolExecutor] = None

_user_sem_lock = threading.Lock()
_user_semaphores: dict[str, asyncio.Semaphore] = {}


def tool_executor() -> ThreadPoolExecutor:
    """Return the lazily-initialised dedicated executor for tool dispatch."""
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=_TOOL_EXECUTOR_MAX_WORKERS,
                    thread_name_prefix="tool",
                )
                logger.info(
                    "tool_executor created (max_workers=%d)",
                    _TOOL_EXECUTOR_MAX_WORKERS,
                )
    return _executor


def shutdown_tool_executor(*, wait: bool = False) -> None:
    """Tear the executor down. Called from FastAPI lifespan shutdown and from
    tests that want a clean slate between cases.
    """
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=wait, cancel_futures=True)
            _executor = None


def get_user_semaphore(
    user_oid: str, max_concurrent: int = _DEFAULT_USER_MAX_CONCURRENT
) -> asyncio.Semaphore:
    """Return the asyncio semaphore for a given user, creating it on first use.

    Bounded per user so one chatty user can't monopolise the tool executor.
    The semaphore object is created inside the running event loop (Semaphore
    captures the loop at construction time) so each FastAPI request lifetime
    sees a consistent semaphore for that user across its tool calls.

    `max_concurrent` is the cap *per user*. It can be tuned but should stay
    well below `_TOOL_EXECUTOR_MAX_WORKERS / expected_concurrent_users` so a
    handful of busy users don't fill the pool.
    """
    sem = _user_semaphores.get(user_oid)
    if sem is None:
        with _user_sem_lock:
            sem = _user_semaphores.get(user_oid)
            if sem is None:
                sem = asyncio.Semaphore(max_concurrent)
                _user_semaphores[user_oid] = sem
    return sem


def reset_user_semaphores() -> None:
    """Test hook — drop all known per-user semaphores so each case starts
    from a fresh slot count."""
    with _user_sem_lock:
        _user_semaphores.clear()


async def run_in_tool_executor(
    fn: Callable[..., T], /, *args: Any, **kwargs: Any
) -> T:
    """`asyncio.to_thread`-style helper that runs on the dedicated tool
    executor and copies the current `ContextVar` context into the worker.

    Use this instead of `asyncio.to_thread` for tool dispatch so:
      1. Tool subprocesses don't compete with KB/SQLite work on the default
         executor.
      2. Per-request ContextVars (ARM token, active skill slug) propagate
         to the worker — `to_thread` does this for you via `copy_context()`
         but `run_in_executor` does not, and we want the same guarantee on
         our dedicated pool.
    """
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()

    def _run() -> T:
        return ctx.run(fn, *args, **kwargs)

    return await loop.run_in_executor(tool_executor(), _run)


async def gated_tool_call(
    user_oid: str,
    coro_or_awaitable: Awaitable[T],
    *,
    max_concurrent: int = _DEFAULT_USER_MAX_CONCURRENT,
) -> T:
    """Run an awaitable under the per-user semaphore.

    Wrap your tool-execution awaitable in this if you want the per-user
    concurrency cap to apply. The prefetch path can also acquire/release the
    semaphore directly — this helper exists for the simpler serial branch.
    """
    sem = get_user_semaphore(user_oid, max_concurrent=max_concurrent)
    async with sem:
        return await coro_or_awaitable
