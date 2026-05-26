"""Tests for configuration and settings."""

import os
import pytest
from app.config import AdoWikiSource, Settings


def _base_settings_kwargs():
    """Required-non-default fields shared by most Settings test cases."""
    return dict(
        AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
        AZURE_OPENAI_API_KEY="key",
        ENTRA_TENANT_ID="t",
        ENTRA_API_CLIENT_ID="c",
    )


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


class TestAdoWikiSource:
    """Validation rules for one ADO wiki source record."""

    def _src(self, **overrides):
        defaults = dict(
            label="platform",
            org="https://dev.azure.com/myorg",
            project="Platform",
            wiki="Platform.wiki",
        )
        defaults.update(overrides)
        return AdoWikiSource(**defaults)

    def test_valid_label(self):
        s = self._src(label="platform")
        assert s.label == "platform"

    def test_label_rejects_uppercase(self):
        with pytest.raises(ValueError, match="must match"):
            self._src(label="Platform")

    def test_label_rejects_leading_digit(self):
        with pytest.raises(ValueError, match="must match"):
            self._src(label="2platform")

    def test_label_rejects_underscore(self):
        with pytest.raises(ValueError, match="must match"):
            self._src(label="platform_team")

    def test_label_rejects_path_separator(self):
        with pytest.raises(ValueError, match="must match"):
            self._src(label="ado/wiki")

    def test_label_rejects_too_short(self):
        with pytest.raises(ValueError, match="must match"):
            self._src(label="a")

    def test_label_rejects_too_long(self):
        with pytest.raises(ValueError, match="must match"):
            self._src(label="a" + "b" * 40)  # 41 chars

    def test_label_accepts_hyphen(self):
        s = self._src(label="platform-team")
        assert s.label == "platform-team"


class TestIngestSourcesValidation:
    """Cross-field validation on INGEST_ADO_WIKI_SOURCES."""

    def _source(self, **overrides):
        defaults = dict(
            label="platform",
            org="https://dev.azure.com/myorg",
            project="Platform",
            wiki="Platform.wiki",
        )
        defaults.update(overrides)
        return defaults

    def test_empty_list_default(self):
        s = Settings(**_base_settings_kwargs())
        assert s.INGEST_ADO_WIKI_SOURCES == []

    def test_parses_json_string_from_env(self):
        import json
        json_str = json.dumps([self._source()])
        s = Settings(
            **_base_settings_kwargs(),
            INGEST_ADO_WIKI_SOURCES=json_str,
        )
        assert len(s.INGEST_ADO_WIKI_SOURCES) == 1
        assert s.INGEST_ADO_WIKI_SOURCES[0].label == "platform"

    def test_invalid_json_rejected(self):
        with pytest.raises(ValueError, match="valid JSON"):
            Settings(
                **_base_settings_kwargs(),
                INGEST_ADO_WIKI_SOURCES="{not json",
            )

    def test_accepts_list_of_dicts(self):
        s = Settings(
            **_base_settings_kwargs(),
            INGEST_ADO_WIKI_SOURCES=[self._source()],
        )
        assert s.INGEST_ADO_WIKI_SOURCES[0].label == "platform"

    def test_duplicate_labels_rejected(self):
        with pytest.raises(ValueError, match="Duplicate ADO wiki labels"):
            Settings(
                **_base_settings_kwargs(),
                INGEST_ADO_WIKI_SOURCES=[
                    self._source(label="platform"),
                    self._source(label="platform", project="OtherProj"),
                ],
            )

    def test_duplicate_org_project_wiki_triple_rejected(self):
        with pytest.raises(ValueError, match="same .org, project, wiki."):
            Settings(
                **_base_settings_kwargs(),
                INGEST_ADO_WIKI_SOURCES=[
                    self._source(label="alpha"),
                    self._source(label="beta"),  # same org+project+wiki, different label
                ],
            )

    def test_same_project_different_wiki_allowed(self):
        """Two wikis in one project is a legitimate config (project wiki + code wiki)."""
        s = Settings(
            **_base_settings_kwargs(),
            INGEST_ADO_WIKI_SOURCES=[
                self._source(label="platform-docs", wiki="Platform.wiki"),
                self._source(label="platform-code", wiki="Platform.codewiki"),
            ],
        )
        assert len(s.INGEST_ADO_WIKI_SOURCES) == 2
