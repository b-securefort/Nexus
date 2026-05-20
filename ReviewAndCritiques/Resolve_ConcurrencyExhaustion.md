# Critical Finding: Concurrency Exhaustion

## Overview
To fix a non-blocking event loop issue, synchronous OpenAI SDK calls and Tool executions were wrapped in `asyncio.to_thread()`. Python's default async thread pool is small (typically `min(32, os.cpu_count() + 4)`). If multiple users trigger parallel tool execution, the thread pool will instantly exhaust, freezing the entire FastAPI server and preventing it from responding to health checks or UI requests.

## Recommendation
In the short term, monitor thread pool metrics and consider increasing `max_workers` and adding a user-level semaphore to rate-limit tool execution.
In the long term, migrate to **native async primitives**:
- Stop using threads for subprocesses. Rewrite tools to use `asyncio.create_subprocess_exec` natively.
- Switch the OpenAI client to `AsyncAzureOpenAI` (using the `httpx` async transport).

## Impact of Recommendation
*   **Positive:** The FastAPI event loop will be able to handle thousands of concurrent API and subprocess calls natively without exhausting threads or freezing the server.
*   **Negative/Cost:** High refactoring effort. (The "Color of Functions" problem). The orchestrator, streaming logic, and tool interfaces must be completely rewritten to support native `async`/`await`. Synchronous libraries (like `GitPython` or `msal`) must still be carefully managed to avoid blocking the main loop.
