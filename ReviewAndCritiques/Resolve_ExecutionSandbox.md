# Critical Finding: Agent Execution Sandbox (`run_shell`)

## Overview
The `run_shell` tool executes commands directly inside the backend API container. Even with explicit user approvals and regex injection guards, running arbitrary shell scripts in the same environment that holds the App Configuration credentials, Azure Managed Identity, and MSAL tokens is a severe security risk. A compromised prompt or obfuscated command could lead to a container escape or credential theft.

## Recommendation
Execute `run_shell` commands in a **separate, ephemeral sandbox container** (e.g., via Azure Container Instances or a locked-down sidecar).
- The main backend API sends the script to the sandbox, waits for execution, and retrieves the output.
- The sandbox must have no network access to internal resources, a read-only root filesystem, and no sensitive environment variables.

## Impact of Recommendation
*   **Positive:** Physically isolates the backend API from blast damage. Prevents credential exfiltration and reverse shells from compromising the host infrastructure.
*   **Negative/Cost:** Adds operational complexity. Starting ephemeral containers (ACI) adds latency to the `run_shell` execution time. Requires managing a separate sandbox image with necessary utilities (Python, curl, jq) installed.
