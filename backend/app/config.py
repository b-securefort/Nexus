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

    # DB
    DATABASE_URL: str = "sqlite:///./app.db"

    # App
    APP_ENV: str = "dev"  # dev | prod
    APP_LOG_LEVEL: str = "INFO"
    APP_CORS_ORIGINS: str = "http://localhost:5173"

    # Auth bypass (dev only)
    DEV_AUTH_BYPASS: bool = False

    # Tool config
    TOOL_SHELL_ENABLED: bool = True
    TOOL_AZ_CLI_ENABLED: bool = True
    TOOL_MS_DOCS_ENABLED: bool = True
    TOOL_AZ_COST_ENABLED: bool = True
    TOOL_AZ_MONITOR_ENABLED: bool = True
    TOOL_AZ_REST_ENABLED: bool = True
    TOOL_GENERATE_FILE_ENABLED: bool = True
    TOOL_AZ_DEVOPS_ENABLED: bool = True
    AZ_DEVOPS_ORG: str = ""
    AZ_DEVOPS_PROJECT: str = ""
    TOOL_AZ_POLICY_ENABLED: bool = True
    TOOL_AZ_ADVISOR_ENABLED: bool = True
    TOOL_NETWORK_TEST_ENABLED: bool = True
    TOOL_DIAGRAM_GEN_ENABLED: bool = True
    TOOL_WEB_FETCH_ENABLED: bool = True
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
