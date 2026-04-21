"""Tests for the rate limiter in the chat API."""

import pytest
import time
from app.api.chat import _check_rate_limit, _rate_limit_store


class TestRateLimit:
    def setup_method(self):
        _rate_limit_store.clear()

    def test_under_limit_passes(self):
        for _ in range(29):
            _check_rate_limit("user-rate-test")
        # 29 calls should be fine

    def test_at_limit_raises(self):
        from fastapi import HTTPException
        for _ in range(30):
            _check_rate_limit("user-at-limit")
        with pytest.raises(HTTPException) as exc:
            _check_rate_limit("user-at-limit")
        assert exc.value.status_code == 429

    def test_different_users_independent(self):
        from fastapi import HTTPException
        for _ in range(30):
            _check_rate_limit("user-x")
        # user-y should still be fine
        _check_rate_limit("user-y")  # Should not raise
