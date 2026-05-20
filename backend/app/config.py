"""
Backend configuration via pydantic-settings.
All config is driven by environment variables.
"""

from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional


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

    # KB ingestion (Phase 2a, pilot)
    INGEST_ADO_WIKI_ENABLED: bool = False
    INGEST_ADO_WIKI_ORG: str = ""
    INGEST_ADO_WIKI_PROJECT: str = ""
    INGEST_ADO_WIKI_NAME: str = ""
    INGEST_PDF_LIST_ENABLED: bool = False
    INGEST_PDF_LIST_WIKI_PATH: str = ""

    # DB
    DATABASE_URL: str = "sqlite:///./app.db"

    # App
    APP_ENV: str = "dev"  # dev | prod
    APP_LOG_LEVEL: str = "INFO"
    APP_CORS_ORIGINS: str = "http://localhost:5173"

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

    @field_validator("DEV_AUTH_BYPASS", mode="after")
    @classmethod
    def validate_dev_auth_bypass(cls, v: bool, info) -> bool:
        """DEV_AUTH_BYPASS must be rejected if APP_ENV != dev."""
        if v and info.data.get("APP_ENV") != "dev":
            raise ValueError("DEV_AUTH_BYPASS=true is only allowed when APP_ENV=dev")
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
