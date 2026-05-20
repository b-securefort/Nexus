# Critical Finding: Context Window & Latency Optimization

## Overview
During a heavy tool-execution turn, the agent might loop up to 15 times, pulling in up to 8KB of raw JSON or CLI output per iteration. Passing tens of thousands of tokens of raw tool output back to the Azure OpenAI model for every step causes massive latency spikes (Time-To-First-Token degradation) and confuses the agent with noise (the "Needle in a Haystack" problem).

## Recommendation
Implement a **Hybrid Map-Reduce** approach for the orchestrator:
1.  **Parallel Execution:** Where applicable, execute read-only tools concurrently (e.g., querying 5 Log Analytics workspaces at once) instead of sequentially.
2.  **Streaming Summarization:** If a tool returns > 2KB of text, route the raw output to a fast, cheap LLM (like `gpt-4o-mini`) to extract the key facts and insights *before* appending it to the main `gpt-4o` agent's context.

## Impact of Recommendation
*   **Positive:** Drastically reduces the prompt size sent to the main agent, improving latency and lowering API costs. Agent accuracy improves because it only reads the extracted insights instead of parsing raw JSON.
*   **Negative/Cost:** Requires rewriting parts of the orchestrator to support parallel tool dispatch and intermediate LLM calls. Adds a small latency overhead for the `gpt-4o-mini` summarization step, though it pays for itself by keeping the main prompt small.
