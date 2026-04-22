# Nexus — Prerequisites & Setup Guide

## System Requirements

| Requirement       | Minimum Version    | Check Command                  |
|-------------------|--------------------|--------------------------------|
| Python            | 3.11+              | `python --version`             |
| Node.js           | 20+                | `node --version`               |
| npm               | 9+                 | `npm --version`                |
| Git               | 2.30+              | `git --version`                |
| Azure CLI (az)    | 2.60+ *(optional)* | `az --version`                 |
| OS                | Windows 10+, macOS 12+, Ubuntu 22.04+ | — |

> Azure CLI is optional for basic usage. It is **required** for any Azure tool 
> (Resource Graph, Cost, Monitor, Advisor, Policy, DevOps, REST API).

---

## Azure CLI Extensions (optional, for full tool support)

If you plan to use all tools, install these extensions after installing Azure CLI:

```bash
az extension add --name costmanagement
az extension add --name log-analytics
az extension add --name azure-devops
az extension add --name resource-graph
```

Check installed extensions:
```bash
az extension list --output table
```

---

## Quick Start (3 terminals)

### Terminal 1 — Backend

```bash
cd backend

# Create virtual environment (first time only)
python -m venv .venv

# Activate
# Windows:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and edit environment config
cp .env.example .env
# Edit .env — see "Backend Configuration" section below

# Run
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

### Terminal 2 — Frontend

```bash
cd frontend

# Install dependencies (first time only)
npm install

# Copy and edit environment config
cp .env.example .env
# Set VITE_API_BASE_URL=http://localhost:8002

# Run
npm run dev
```

### Terminal 3 — Terminal Client (optional)

```bash
cd terminal-client

# Create virtual environment (first time only)
python -m venv .venv

# Activate
# Windows:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

---

## Backend Configuration (.env)

Create `backend/.env` with the following (or copy `.env.example`):

```ini
# ── Required: Azure OpenAI ──────────────────────────────────
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-api-key
AZURE_OPENAI_DEPLOYMENT=gpt-4o-mini          # or your deployment name
AZURE_OPENAI_API_VERSION=2025-04-01-preview

# ── Required: Database ──────────────────────────────────────
DATABASE_URL=sqlite:///./app.db

# ── Required: Knowledge Base ────────────────────────────────
KB_REPO_LOCAL_PATH=./kb_data
KB_REPO_LOCAL_ONLY=true                       # set false + provide KB_REPO_URL for Git sync

# ── Dev mode (bypass Entra auth) ────────────────────────────
APP_ENV=dev
DEV_AUTH_BYPASS=true

# ── Entra ID (production only) ──────────────────────────────
ENTRA_TENANT_ID=your-tenant-id
ENTRA_API_CLIENT_ID=your-client-id
ENTRA_API_AUDIENCE=api://your-client-id

# ── Tool toggles ────────────────────────────────────────────
TOOL_SHELL_ENABLED=true
TOOL_AZ_CLI_ENABLED=true
TOOL_MS_DOCS_ENABLED=true
TOOL_AZ_COST_ENABLED=true
TOOL_AZ_MONITOR_ENABLED=true
TOOL_AZ_REST_ENABLED=true
TOOL_GENERATE_FILE_ENABLED=true
TOOL_AZ_DEVOPS_ENABLED=true
TOOL_AZ_POLICY_ENABLED=true
TOOL_AZ_ADVISOR_ENABLED=true
TOOL_NETWORK_TEST_ENABLED=true
TOOL_DIAGRAM_GEN_ENABLED=true
TOOL_WEB_FETCH_ENABLED=true

# ── Misc ────────────────────────────────────────────────────
APP_LOG_LEVEL=INFO
APP_CORS_ORIGINS=http://localhost:5173,http://localhost:5174
TOOL_APPROVAL_TIMEOUT_SECONDS=600
CHAT_RATE_LIMIT_PER_MINUTE=30
BACKUP_ENABLED=false
```

## Frontend Configuration (.env)

Create `frontend/.env`:

```ini
VITE_API_BASE_URL=http://localhost:8002
VITE_DEV_AUTH_BYPASS=true

# Production only (Entra ID):
# VITE_ENTRA_TENANT_ID=your-tenant-id
# VITE_ENTRA_CLIENT_ID=your-client-id
# VITE_ENTRA_API_SCOPE=api://your-client-id/user_impersonation
```

---

## Running Tests

### Backend (pytest — 234 tests)

```bash
cd backend
# Activate venv first
python -m pytest tests/ -x -q
```

### Frontend (vitest — 109 tests)

```bash
cd frontend
npm test
```

### E2E Tests (requires running backend)

```bash
# Start backend first in another terminal, then:
cd terminal-client
python e2e_chat_test.py           # 20 basic tests
python e2e_advanced_test.py       # 10 chained-tool tests
python e2e_multitool_test.py      # 20 multi-tool integration tests
```

---

## Azure Login (for Azure tools)

Before using any Azure tool, authenticate:

```bash
az login --use-device-code
```

Verify:
```bash
az account show --output table
```

The tools will auto-detect login state and prompt you if not authenticated.

---

## Ports

| Service          | Default Port |
|------------------|-------------|
| Backend (API)    | 8002        |
| Frontend (Vite)  | 5173        |
| Terminal Client  | N/A (CLI)   |

If port 8002 is taken, change it in the uvicorn command and update 
`VITE_API_BASE_URL` in `frontend/.env` to match.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError` | Activate your venv: `.\.venv\Scripts\Activate.ps1` (Windows) or `source .venv/bin/activate` |
| Backend won't start — port in use | `netstat -ano \| findstr :8002` then `taskkill /PID <pid> /F` |
| `az` not found on Windows | Use `az.cmd` or add Azure CLI to PATH. Restart terminal. |
| CORS errors in browser | Check `APP_CORS_ORIGINS` includes your frontend URL |
| Tests fail with "sqlite" errors | Make sure CWD is `backend/` when running tests |
| Azure tools return "not logged in" | Run `az login --use-device-code` |
| Cost tool fails | Run `az extension add --name costmanagement` |
| Monitor tool fails | Run `az extension add --name log-analytics` |
| DevOps tool fails | Run `az extension add --name azure-devops` |
