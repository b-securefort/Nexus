# Run Nexus Locally

Start the Nexus backend and frontend dev servers for local development.

## Backend

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --port 8002
```

The backend runs on **http://localhost:8002**. Verify with:
```powershell
curl http://localhost:8002/healthz
```

## Frontend

```powershell
cd frontend
npm run dev
```

The frontend runs on **http://localhost:5174** (Vite dev server). It proxies API calls to the backend.

## Workflow

1. Start the **backend first**, then the frontend.
2. Verify the backend is healthy before starting the frontend.
3. If a port is in use, check for existing processes rather than killing blindly:
   ```powershell
   netstat -ano | findstr :8002
   ```
4. Both `backend/.env` and `frontend/.env` must have `DEV_AUTH_BYPASS=true` for local dev without Entra auth.

## Common issues

- If `uvicorn` is not found, the venv may not be activated — run `.\.venv\Scripts\Activate.ps1` first.
- If the frontend shows API errors, check `frontend/.env` has `VITE_API_BASE_URL=http://localhost:8002`.
- If the DB schema is out of date, run `alembic upgrade head` from `backend/` before starting the server.
