# Nexus — Team Architect Assistant

A self-hosted, team-oriented AI assistant that serves a small team via a web interface, powered by Azure OpenAI. Chat with a shared Markdown knowledge base through configurable skills.

## Quick Start (Local Development)

### Prerequisites

- Python 3.11+
- Node.js 20+
- Git

### 1. Clone and configure

```bash
cd team-architect-app
cp .env.example backend/.env
# Edit backend/.env with your values (defaults work for dev with auth bypass)
```

### 2. Backend

```bash
cd backend
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000 --app-dir .
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev   # Vite on port 5173
```

### 4. Open

Navigate to http://localhost:5173

## Dev Auth Bypass

By default, `DEV_AUTH_BYPASS=true` in the backend `.env` injects a fake user so you don't need Entra ID app registrations for development. This is automatically rejected in production (`APP_ENV=prod`).

## Architecture

- **Backend:** FastAPI (Python 3.11+) with SQLite
- **Frontend:** React 18 + Vite + TypeScript + Tailwind CSS
- **LLM:** Azure OpenAI (gpt-5.4-mini)
- **Auth:** Microsoft Entra ID (Azure AD) via MSAL
- **KB:** Git-backed Markdown knowledge base

## Key Features

- **Skills:** Configurable system prompts + tool allowlists (shared via Git, personal via DB)
- **Tools:** KB search/read, Microsoft Docs fetch, shell execution, Azure CLI
- **Approval gating:** Dangerous tools (shell, az cli) require explicit user approval
- **SSE streaming:** Real-time streaming of LLM responses
- **Conversation persistence:** SQLite-backed conversation history

## Environment Variables

See `.env.example` for the complete list with descriptions.

## Entra ID App Registration Setup

Two app registrations are needed for production:

### Backend API (`team-architect-api`)
1. Register a new app in Azure AD
2. Expose an API with scope `user_impersonation`
3. Set Application ID URI to `api://<client-id>`

### Frontend SPA (`team-architect-web`)
1. Register a new app with SPA platform
2. Set redirect URIs: `http://localhost:5173` (dev), `https://<prod-host>` (prod)
3. Add delegated permission to backend API's `user_impersonation`
4. Grant admin consent

## KB Repository Structure

```
team-kb/
├── kb/
│   ├── adrs/*.md
│   ├── patterns/*.md
│   ├── runbooks/*.md
│   ├── snippets/*.md
│   └── platform/*.md
├── skills/shared/
│   └── <skill-name>/SKILL.md
└── kb_index.json
```

## Skill Authoring

### Shared Skills (SKILL.md)

```markdown
---
display_name: My Skill
description: What this skill does
tools:
  - read_kb_file
  - search_kb
---

System prompt content here...
```

### Personal Skills

Create via the web UI at `/skills`.

## Deployment

See `Nexus_PRD.md` §15 for Azure Container Apps deployment details.
