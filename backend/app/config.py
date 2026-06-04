"""
Backend configuration via pydantic-settings.
All config is driven by environment variables.
"""

import json
import re

from pydantic_settings import BaseSettings
from pydantic import BaseModel, field_validator
from typing import Optional


_LABEL_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")


class AdoWikiSource(BaseModel):
    """One configured ADO wiki ingestion source. See DESIGN.md §5
    2026-05-26 "Multi-wiki ADO ingestion".

    `label` is a stable user-chosen identifier (not derived from `project`)
    that names the on-disk subdirectory kb_data/kb/ado_wiki/<label>/ and the
    `source_instance` column on every chunk. Convention: slug of the ADO
    project name, your choice, never changes — Nexus does not enforce the
    convention but does enforce the regex and uniqueness.
    """
    label: str
    org: str
    project: str
    wiki: str

    @field_validator("label")
    @classmethod
    def validate_label(cls, v: str) -> str:
        if not _LABEL_RE.match(v):
            raise ValueError(
                f"label {v!r} must match {_LABEL_RE.pattern} "
                "(lowercase letter, then 1-39 of [a-z0-9-])"
            )
        return v


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-5.4-mini"
    AZURE_OPENAI_API_VERSION: str = "2024-10-21"
    AZURE_OPENAI_EMBED_DEPLOYMENT: str = "text-embedding-3-small"
    AZURE_OPENAI_EMBED_API_VERSION: str = "2023-05-15"
    # Context window of the chat deployment in tokens. Surfaced to the frontend
    # via the `done` SSE event so the chat UI can show "X / Y tokens (Z%)".
    # Update alongside AZURE_OPENAI_DEPLOYMENT when the underlying model changes.
    AZURE_OPENAI_CONTEXT_WINDOW_TOKENS: int = 128000

    # Entra ID
    ENTRA_TENANT_ID: str = ""
    ENTRA_API_CLIENT_ID: str = ""
    ENTRA_API_AUDIENCE: str = ""

    # KB Git repo
    KB_REPO_URL: str = ""
    KB_REPO_BRANCH: str = "main"
    KB_REPO_LOCAL_PATH: str = "./kb_data"
    KB_REPO_AUTH_METHOD: str = "pat"  # pat | managed_identity
    KB_REPO_PAT: str = ""
    KB_SYNC_INTERVAL_SECONDS: int = 900
    KB_REPO_LOCAL_ONLY: bool = False

    # KB local hybrid retrieval (Phase 2)
    # Embeddings: Azure OpenAI text-embedding-3-small (1536 dims).
    # Uses the same AZURE_OPENAI_* credentials as the chat path — no extra config needed.
    KB_EMBED_DIMENSIONS: int = 1536      # must match vec0 schema; changing requires force_rebuild
    KB_CHUNK_MAX_CHARS: int = 6000
    KB_CHUNK_OVERLAP_FRACTION: float = 0.15
    KB_BM25_TOP_K: int = 50
    KB_VEC_TOP_K: int = 50
    KB_RRF_K: int = 60
    KB_RESULT_LIMIT: int = 5

    # Re-ranker (Phase 2b). When enabled, search_kb_hybrid runs an LLM-judge
    # over the top RRF candidates so the final confidence comes from a
    # query-vs-chunk relevance judgement rather than raw vector distance.
    # Disable to save the extra LLM call when latency matters more than
    # ranking quality.
    KB_RERANK_ENABLED: bool = True
    KB_RERANK_TOP_K: int = 10              # how many RRF candidates to rerank
    KB_RERANK_HIGH_THRESHOLD: float = 0.70  # rerank_score >= this -> "high" confidence
    KB_RERANK_MEDIUM_THRESHOLD: float = 0.40  # rerank_score >= this -> "medium" confidence

    # Diversity cap: max chunks from the same file in the final top-K.
    # Stops cross-cutting queries returning 3-4 chunks from one big doc when
    # 2 other docs also have relevant content. Set to 0 to disable.
    KB_DIVERSITY_MAX_PER_FILE: int = 2

    # KB ingestion (Phase 2a)
    # ADO wiki ingestion is list-driven — empty list means disabled. Each
    # entry is a JSON object with {label, org, project, wiki}; see the
    # AdoWikiSource model above for field validation. Authentication for all
    # sources uses the global KB_REPO_PAT (org-level Wiki(read) scope).
    # Format in .env:
    #   INGEST_ADO_WIKI_SOURCES='[{"label":"platform","org":"https://dev.azure.com/myorg","project":"Platform","wiki":"Platform.wiki"}]'
    INGEST_ADO_WIKI_SOURCES: list[AdoWikiSource] = []
    INGEST_PDF_LIST_ENABLED: bool = False
    INGEST_PDF_LIST_WIKI_PATH: str = ""

    # DB
    DATABASE_URL: str = "sqlite:///./app.db"

    # App
    APP_ENV: str = "dev"  # dev | prod
    APP_LOG_LEVEL: str = "INFO"
    APP_CORS_ORIGINS: str = "http://localhost:5173"

    # ── Phase gating (see app/phases.py and gatesreadme.md) ────────────────
    # Temporary phased-rollout control. Higher number = more features unlocked.
    # At full rollout this setting + the entire app/phases.py module + every
    # is_enabled() / is_tool_enabled() / is_skill_enabled() call-site wrapper
    # are deleted together. See gatesreadme.md for the removal playbook.
    #
    # Phase 0 — KB + docs only (read-only knowledge buddy)
    # Phase 1 — adds az_resource_graph (read-only Azure)
    # Phase 2 — adds az_cli + execute_script (approval-gated) + chat-with-kb skill
    # Phase 3 — adds personal skills + architect + drawio-diagrammer skills
    #
    # Default of 1 ships P0+P1 to a fresh deployment with no NEXUS_PHASE set.
    NEXUS_PHASE: int = 1

    # Auth bypass (dev only)
    DEV_AUTH_BYPASS: bool = False

    # Role-based access (see app/auth/rbac.py).
    # When AZURE_APPCONFIG_ENDPOINT is empty the hardcoded DEFAULT_ACCESS_MAP
    # in rbac.py is used — that is the safe production path until App Config
    # is provisioned. The endpoint is the App Config resource URL, e.g.
    # https://my-appconfig.azconfig.io. Auth is via Managed Identity
    # (DefaultAzureCredential).
    AZURE_APPCONFIG_ENDPOINT: str = ""
    AZURE_APPCONFIG_ROLE_KEY: str = "Nexus:RoleAccessMap"

    # Tool bundles — set to false to disable an entire team-specific bundle
    TOOL_BUNDLE_AZURE_ENABLED: bool = True   # set false for non-Azure team deployments

    # Auto-nudge the model when it announces an action but emits no tool call.
    # When True, the orchestrator detects deferred-action language at the end
    # of an assistant turn with no tool_calls and re-enters the loop with a
    # synthetic system reminder (capped at one nudge per chat turn). Set to
    # False if false positives are hurting more than narration is.
    NARRATION_NUDGE_ENABLED: bool = True

    # Tool config
    TOOL_SEARCH_SEMANTIC_ENABLED: bool = True
    TOOL_SEARCH_STACKOVERFLOW_ENABLED: bool = True
    STACKOVERFLOW_API_KEY: str = ""  # optional — raises daily quota from 300 to 10,000
    TOOL_SEARCH_GITHUB_ENABLED: bool = True
    GITHUB_TOKEN: str = ""  # optional — raises rate limit from 10 to 30 req/min
    TOOL_SEARCH_AZURE_UPDATES_ENABLED: bool = True
    TOOL_WEB_SEARCH_ENABLED: bool = True
    TOOL_SHELL_ENABLED: bool = True
    TOOL_AZ_CLI_ENABLED: bool = True
    TOOL_MS_DOCS_ENABLED: bool = True
    TOOL_AZ_COST_ENABLED: bool = True
    TOOL_AZ_MONITOR_ENABLED: bool = True
    TOOL_AZ_REST_ENABLED: bool = True
    TOOL_GENERATE_FILE_ENABLED: bool = True
    TOOL_VALIDATE_DRAWIO_ENABLED: bool = True
    TOOL_AZ_DEVOPS_ENABLED: bool = True
    AZ_DEVOPS_ORG: str = ""
    AZ_DEVOPS_PROJECT: str = ""
    TOOL_AZ_POLICY_ENABLED: bool = True
    TOOL_AZ_ADVISOR_ENABLED: bool = True
    TOOL_NETWORK_TEST_ENABLED: bool = True
    TOOL_DIAGRAM_GEN_ENABLED: bool = True
    TOOL_WEB_FETCH_ENABLED: bool = True
    TOOL_RENDER_DRAWIO_ENABLED: bool = True
    TOOL_PYTHON_DIAGRAM_ENABLED: bool = True
    TOOL_DRAWIO_FROM_PYTHON_ENABLED: bool = True
    # Optional: HTTP endpoint of a drawio-image-export2 sidecar.
    # When set, render_drawio POSTs the XML to this URL instead of calling
    # the local draw.io desktop CLI. Use this for containerized deployments
    # (Container Apps, App Service Linux multi-container, AKS).
    # Example: http://drawio-export:8080
    DRAWIO_EXPORT_URL: str = ""
    DRAWIO_EXPORT_TIMEOUT_SECONDS: int = 30
    TOOL_APPROVAL_TIMEOUT_SECONDS: int = 600

    # Backup
    BACKUP_ENABLED: bool = False
    BACKUP_AZURE_STORAGE_CONNECTION_STRING: str = ""
    BACKUP_CONTAINER_NAME: str = "sqlite-backups"
    BACKUP_INTERVAL_SECONDS: int = 86400

    # Uploads
    UPLOAD_DIR: str = "./uploads"
    UPLOAD_MAX_FILE_SIZE_MB: int = 5
    UPLOAD_MAX_FILES_PER_MESSAGE: int = 4

    # Rate limiting
    CHAT_RATE_LIMIT_PER_MINUTE: int = 30

    # Azure OpenAI circuit breaker + timeouts
    # AOAI_CB_FAILURE_THRESHOLD — consecutive failures within AOAI_CB_WINDOW_SECONDS before opening
    # AOAI_CB_OPEN_SECONDS      — how long the circuit stays open before a half-open probe
    # AOAI_TIMEOUT_SECONDS      — per-request HTTP timeout for all completions calls
    AOAI_CB_FAILURE_THRESHOLD: int = 5
    AOAI_CB_WINDOW_SECONDS: int = 60
    AOAI_CB_OPEN_SECONDS: int = 30
    AOAI_TIMEOUT_SECONDS: int = 60

    # Approval-card risk assessment (separate review LLM; advisory only — never
    # gates execution). RISK_REVIEW_ENABLED=false disables the review call and
    # the card falls back to the deterministic-floor verdict only.
    # RISK_REVIEW_TIMEOUT_SECONDS caps the review so the approval card resolves
    # fast; on timeout/error the verdict fails closed to "caution".
    RISK_REVIEW_ENABLED: bool = True
    RISK_REVIEW_TIMEOUT_SECONDS: float = 4.0

    @field_validator("DEV_AUTH_BYPASS", mode="after")
    @classmethod
    def validate_dev_auth_bypass(cls, v: bool, info) -> bool:
        """DEV_AUTH_BYPASS must be rejected if APP_ENV != dev."""
        if v and info.data.get("APP_ENV") != "dev":
            raise ValueError("DEV_AUTH_BYPASS=true is only allowed when APP_ENV=dev")
        return v

    @field_validator("INGEST_ADO_WIKI_SOURCES", mode="before")
    @classmethod
    def parse_sources_json(cls, v):
        """Accept a JSON string from .env or a list/dict from python code."""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"INGEST_ADO_WIKI_SOURCES must be valid JSON: {e}"
                )
        return v

    @field_validator("INGEST_ADO_WIKI_SOURCES", mode="after")
    @classmethod
    def validate_sources_uniqueness(cls, v: list[AdoWikiSource]) -> list[AdoWikiSource]:
        """Reject duplicate labels or duplicate (org, project, wiki) triples."""
        labels = [s.label for s in v]
        if len(set(labels)) != len(labels):
            dupes = sorted({l for l in labels if labels.count(l) > 1})
            raise ValueError(f"Duplicate ADO wiki labels: {dupes}")
        triples = [(s.org.rstrip("/"), s.project, s.wiki) for s in v]
        if len(set(triples)) != len(triples):
            raise ValueError(
                "Two source records point at the same (org, project, wiki) — "
                "pick one label and delete the other"
            )
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.APP_CORS_ORIGINS.split(",") if o.strip()]

    model_config = {
        "env_file": [".env", "backend/.env"],
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
