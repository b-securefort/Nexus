"""Tests for authentication module."""

import pytest
from app.auth.models import User
from app.auth.entra import get_current_user
from unittest.mock import AsyncMock, MagicMock


class TestDevAuthBypass:
    """Test DEV_AUTH_BYPASS functionality."""

    @pytest.mark.asyncio
    async def test_dev_bypass_returns_dev_user(self):
        """DEV_AUTH_BYPASS=true should return a fake dev user."""
        request = MagicMock()
        user = await get_current_user(request)
        assert user.oid == "dev-user"
        assert user.email == "dev@local"
        assert user.display_name == "Dev User"

    def test_user_model(self):
        user = User(oid="abc", email="test@test.com", display_name="Test")
        assert user.oid == "abc"
        assert user.email == "test@test.com"


class TestProdAuthBypassRejection:
    """Test that DEV_AUTH_BYPASS is rejected in prod."""

    def test_bypass_rejected_in_prod(self):
        from app.config import Settings
        with pytest.raises(ValueError, match="only allowed when APP_ENV=dev"):
            Settings(APP_ENV="prod", DEV_AUTH_BYPASS=True)
