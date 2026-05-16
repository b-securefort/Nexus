# Run Nexus Locally

Start the Nexus backend and frontend dev servers for local development.

## Backend

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --port 8000
```

The backend runs on **http://localhost:8000**. Verify with:
```powershell
curl http://localhost:8000/healthz
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
3. On startup, the KB reindexer runs in the background — it chunks and embeds all
   `*.md` files under `kb_data/kb/`. Check progress at:
   ```powershell
   curl http://localhost:8000/api/kb/index/status
   ```
   Wait for `"state": "complete"` before running KB hybrid search queries. First-run
   indexing takes ~5-15 seconds (one Azure OpenAI embedding API call). Subsequent
   restarts are near-instant — only changed files are re-embedded.
4. If a port is in use, check for existing processes rather than killing blindly:
   ```powershell
   netstat -ano | findstr :8002
   ```
5. Both `backend/.env` and `frontend/.env` must have `DEV_AUTH_BYPASS=true` for
   local dev without Entra auth.

## Key endpoints

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Backend health check |
| `GET /api/kb/index/status` | KB reindex progress (state, indexed_files, total_files, errors) |
| `POST /api/kb/index/rebuild` | Force a full re-embed of all KB files (use after changing KB_CHUNK_MAX_CHARS or swapping the embed model) |
| `GET /metrics` | Prometheus metrics |

## Verifying vector search works

Run these in order after startup. All three should pass before testing via the UI.

**Step 1 — Index status (API)**
```powershell
curl http://localhost:8000/api/kb/index/status
```
Expected: `"state": "complete"`, `"indexed_files"` > 0, `"errors": []`.
If errors appear, the filename in the error tells you which file failed and why.

**Step 2 — DB sanity check (Python, run from `backend/`)**
```powershell
python -c "
import sqlite3, re
c = sqlite3.connect('app.db')
chunks = c.execute('SELECT COUNT(*) FROM kb_chunks').fetchone()[0]
sql    = c.execute(\"SELECT sql FROM sqlite_master WHERE name='kb_chunks_vec'\").fetchone()[0]
dims   = int(re.search(r'float\[(\d+)\]', sql).group(1))
bytes_ = c.execute('SELECT length(embedding) FROM kb_chunks_vec LIMIT 1').fetchone()[0]
print(f'Chunks: {chunks}')
print(f'vec0 schema: float[{dims}]  (expected 1536)')
print(f'Embedding bytes: {bytes_}  (expected {1536*4} = 6144)')
"
```
Expected output:
```
Chunks: <N>          ← any number > 0
vec0 schema: float[1536]
Embedding bytes: 6144
```
If `vec0 schema` shows `float[384]` the dimension mismatch fix didn't run — stop
the server, delete `app.db`, and restart.

**Step 3 — Live search via the UI**

Open the frontend, select the **KB Searcher** skill, and ask:
> "What is RRF and how does it work?"

The agent should call `search_kb_hybrid` (visible in the tool-call stream), return
chunk-level snippets with `kb_path` values, and **not** fall back to
`search_kb_semantic`. If it falls back, the index is still warming — wait for
Step 1 to report `"state": "complete"` and retry.

## Common issues

- **`uvicorn` not found** — venv not activated. Run `.\.venv\Scripts\Activate.ps1` first.
- **Frontend API errors** — check `frontend/.env` has `VITE_API_BASE_URL=http://localhost:8000`.
- **`search_kb_hybrid` reports "unavailable"** — the Python build was compiled without
  `enable_load_extension` (common with Anaconda). Use the [python.org Windows installer](https://python.org/downloads/)
  instead. The standard `az_cli` / `search_kb` tools still work; only the local vector
  search is affected.
- **KB index empty after startup** — check logs for `Reindex error` lines. Most likely
  cause: `AZURE_OPENAI_API_KEY` is missing or wrong in `backend/.env`. Fix the key and
  call `POST /api/kb/index/rebuild` to re-trigger without restarting.
- **DB schema out of date** — the startup shim in `main.py` adds missing columns
  automatically on every start. You should not need to run Alembic manually in dev.
  If you see a column-missing error, restart the server — the shim will fix it.
