# Critical Finding: Stateful Approvals & Container Restarts

## Overview
When a tool requires approval, the agent loop pauses execution using an in-memory `asyncio.Event` while waiting for the user to click "Approve" (a window of up to 10 minutes). If the backend container restarts, scales out, or crashes during this window, the in-memory loop is destroyed. When the user approves via the API, the DB updates, but the loop cannot resume, leaving the chat permanently hung.

## Recommendation
Refactor the orchestrator to be **fully stateless**.
- When an approval is required, persist the current conversation history and pending tool call to SQLite, and immediately terminate the agent loop.
- When the `POST /api/approvals` handler receives the approval, it re-hydrates the context from the DB and triggers a new LLM generation loop to continue the work.

## Impact of Recommendation
*   **Positive:** Makes the application robust against pod crashes and allows for 100% horizontal scalability. Users will not experience dropped chats during deployments.
*   **Negative/Cost:** Moderate refactoring effort. The orchestrator must be capable of resuming execution exactly where it left off based solely on database state, requiring careful handling of the prompt payload reconstruction.
