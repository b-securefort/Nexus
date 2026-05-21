"""Tests for authentication module."""

import jwt
import pytest
from app.auth.models import User
from app.auth.entra import _extract_arm_token, get_current_user
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


# ── ARM token pass-through guardrails ──────────────────────────────────────
# _extract_arm_token reads claims from an unverified JWT. These tests are a
# guardrail for future maintainers: they assert the function returns the raw
# token bytes unchanged (no parsed claim is ever surfaced) and that only the
# two pass-through filters (audience + tenant) gate acceptance. Any claim
# that *isn't* `aud` or `tid` (e.g. `roles`, `oid`, `groups`) must NOT
# influence the outcome — those would be authorization decisions on data we
# never verified, exactly the trap the SECURITY note in entra.py warns about.


def _make_request(arm_token_header: str | None):
    """Build a MagicMock request with the given X-ARM-Token header value."""
    req = MagicMock()
    headers = {} if arm_token_header is None else {"X-ARM-Token": arm_token_header}
    req.headers = headers
    # MagicMock.headers.get won't honor the dict; use a real dict-like below.
    req.headers = _DictHeaders(headers)
    return req


class _DictHeaders(dict):
    def get(self, k, default=""):
        return super().get(k, default)


class TestExtractArmTokenGuardrails:
    TENANT = "00000000-0000-0000-0000-000000000001"
    OTHER_TENANT = "00000000-0000-0000-0000-000000000999"

    @staticmethod
    def _encode(payload: dict) -> str:
        # verify_signature=False on the read side means any signature works.
        return jwt.encode(payload, "irrelevant-secret", algorithm="HS256")

    def test_missing_header_returns_none(self):
        req = _make_request(None)
        assert _extract_arm_token(req, self.TENANT) is None

    def test_empty_header_returns_none(self):
        req = _make_request("   ")
        assert _extract_arm_token(req, self.TENANT) is None

    def test_valid_token_returns_raw_unchanged(self):
        """The returned value is the verbatim header — no parsed claims."""
        raw = self._encode({
            "aud": "https://management.azure.com/",
            "tid": self.TENANT,
        })
        req = _make_request(raw)
        assert _extract_arm_token(req, self.TENANT) == raw

    def test_wrong_audience_rejected(self):
        raw = self._encode({
            "aud": "https://graph.microsoft.com/",
            "tid": self.TENANT,
        })
        req = _make_request(raw)
        assert _extract_arm_token(req, self.TENANT) is None

    def test_wrong_tenant_rejected(self):
        raw = self._encode({
            "aud": "https://management.azure.com/",
            "tid": self.OTHER_TENANT,
        })
        req = _make_request(raw)
        assert _extract_arm_token(req, self.TENANT) is None

    def test_extra_claims_are_ignored(self):
        """Crafted claims like `roles` or `oid` must NOT change behavior.

        If a future change ever started consuming these unverified claims for
        an authorization decision, this test would still pass — but the test
        below (`test_only_aud_and_tid_are_consulted`) asserts intent at the
        implementation level.
        """
        raw = self._encode({
            "aud": "https://management.azure.com/",
            "tid": self.TENANT,
            "roles": ["admin", "owner"],
            "oid": "attacker-oid",
            "groups": ["super-admins"],
        })
        req = _make_request(raw)
        # Same outcome as the minimal-claims case: returned, but as opaque bytes
        assert _extract_arm_token(req, self.TENANT) == raw

    def test_extra_claims_cannot_override_audience_check(self):
        """Even a forged `roles=admin` claim cannot rescue a wrong-aud token."""
        raw = self._encode({
            "aud": "https://attacker.example.com/",
            "tid": self.TENANT,
            "roles": ["admin"],
        })
        req = _make_request(raw)
        assert _extract_arm_token(req, self.TENANT) is None

    def test_malformed_token_returns_none(self):
        req = _make_request("not.a.jwt")
        assert _extract_arm_token(req, self.TENANT) is None

    def test_only_aud_and_tid_are_consulted(self):
        """Implementation-level guardrail: the source of _extract_arm_token
        must not reference claims other than `aud` and `tid`. Any new claim
        access becomes a code-review trigger via this failing test.
        """
        import inspect
        from app.auth import entra
        src = inspect.getsource(entra._extract_arm_token)
        # Strip the docstring so its prose (which discusses untrusted oid /
        # roles / groups) does not trip the substring check below.
        import re as _re
        src_no_doc = _re.sub(r'""".*?"""', "", src, count=1, flags=_re.DOTALL)
        forbidden = ("claims.get(\"oid\"", "claims.get('oid'",
                     "claims.get(\"roles\"", "claims.get('roles'",
                     "claims.get(\"groups\"", "claims.get('groups'",
                     "claims.get(\"sub\"", "claims.get('sub'",
                     "claims.get(\"upn\"", "claims.get('upn'")
        for needle in forbidden:
            assert needle not in src_no_doc, (
                f"_extract_arm_token reads {needle!r} — these claims are "
                f"unverified and must not be used for authorization. See the "
                f"SECURITY note in entra.py."
            )
