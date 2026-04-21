"""Tests for configuration and settings."""

import os
import pytest
from app.config import Settings


class TestSettings:
    def test_default_values(self):
        s = Settings(
            AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
            AZURE_OPENAI_API_KEY="key",
            ENTRA_TENANT_ID="t",
            ENTRA_API_CLIENT_ID="c",
        )
        assert s.APP_ENV == "dev"
        assert s.AZURE_OPENAI_DEPLOYMENT == "gpt-5.4-mini"
        assert s.KB_REPO_LOCAL_PATH == "./kb_data"
        assert s.CHAT_RATE_LIMIT_PER_MINUTE == 30
        assert s.TOOL_APPROVAL_TIMEOUT_SECONDS == 600
        assert s.KB_SYNC_INTERVAL_SECONDS == 900

    def test_cors_origins_list_single(self):
        s = Settings(
            AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
            AZURE_OPENAI_API_KEY="key",
            ENTRA_TENANT_ID="t",
            ENTRA_API_CLIENT_ID="c",
            APP_CORS_ORIGINS="http://localhost:5173",
        )
        assert s.cors_origins_list == ["http://localhost:5173"]

    def test_cors_origins_list_multiple(self):
        s = Settings(
            AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
            AZURE_OPENAI_API_KEY="key",
            ENTRA_TENANT_ID="t",
            ENTRA_API_CLIENT_ID="c",
            APP_CORS_ORIGINS="http://localhost:5173,https://app.example.com",
        )
        assert len(s.cors_origins_list) == 2

    def test_dev_bypass_allowed_in_dev(self):
        s = Settings(
            APP_ENV="dev",
            DEV_AUTH_BYPASS=True,
            AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
            AZURE_OPENAI_API_KEY="key",
            ENTRA_TENANT_ID="t",
            ENTRA_API_CLIENT_ID="c",
        )
        assert s.DEV_AUTH_BYPASS is True

    def test_dev_bypass_rejected_in_prod(self):
        with pytest.raises(ValueError, match="only allowed when APP_ENV=dev"):
            Settings(
                APP_ENV="prod",
                DEV_AUTH_BYPASS=True,
                AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
                AZURE_OPENAI_API_KEY="key",
                ENTRA_TENANT_ID="t",
                ENTRA_API_CLIENT_ID="c",
            )

    def test_prod_without_bypass(self):
        s = Settings(
            APP_ENV="prod",
            DEV_AUTH_BYPASS=False,
            AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
            AZURE_OPENAI_API_KEY="key",
            ENTRA_TENANT_ID="t",
            ENTRA_API_CLIENT_ID="c",
        )
        assert s.APP_ENV == "prod"
        assert s.DEV_AUTH_BYPASS is False
