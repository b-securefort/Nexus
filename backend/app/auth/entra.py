"""
Entra ID token validation and user extraction.
Supports DEV_AUTH_BYPASS for local development.
"""

import logging
import time
from typing import Optional

import httpx
import jwt
from fastapi import HTTPException, Request

from app.auth.models import User
from app.config import get_settings

logger = logging.getLogger(__name__)

# Cache for JWKS keys
_jwks_cache: Optional[dict] = None
_jwks_cache_time: float = 0
_JWKS_CACHE_TTL = 3600  # 1 hour


async def _fetch_jwks(tenant_id: str) -> dict:
    """Fetch JWKS keys from Entra ID, with caching."""
    global _jwks_cache, _jwks_cache_time

    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < _JWKS_CACHE_TTL:
        return _jwks_cache

    oidc_url = f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
    async with httpx.AsyncClient() as client:
        oidc_resp = await client.get(oidc_url)
        oidc_resp.raise_for_status()
        jwks_uri = oidc_resp.json()["jwks_uri"]

        jwks_resp = await client.get(jwks_uri)
        jwks_resp.raise_for_status()
        _jwks_cache = jwks_resp.json()
        _jwks_cache_time = now
        return _jwks_cache


def _decode_token(token: str, jwks: dict, settings) -> dict:
    """Decode and validate a JWT token against JWKS."""
    # Find the signing key
    unverified_header = jwt.get_unverified_header(token)
    kid = unverified_header.get("kid")

    rsa_key = None
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            rsa_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
            break

    if not rsa_key:
        raise HTTPException(status_code=401, detail="Unable to find signing key")

    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=settings.ENTRA_API_AUDIENCE,
            issuer=[
                f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}/v2.0",
                f"https://sts.windows.net/{settings.ENTRA_TENANT_ID}/",
            ],
            options={"verify_exp": True},
        )

        # Validate tenant
        if payload.get("tid") != settings.ENTRA_TENANT_ID:
            raise HTTPException(status_code=401, detail="Invalid tenant")

        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidAudienceError:
        raise HTTPException(status_code=401, detail="Invalid audience")
    except jwt.InvalidIssuerError:
        raise HTTPException(status_code=401, detail="Invalid issuer")
    except jwt.PyJWTError as e:
        logger.warning("JWT validation failed: %s", str(e))
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(request: Request) -> User:
    """Extract and validate user from the Authorization header."""
    settings = get_settings()

    # Dev auth bypass
    if settings.DEV_AUTH_BYPASS:
        if settings.APP_ENV != "dev":
            # This should never happen due to validator, but belt and suspenders
            raise HTTPException(status_code=500, detail="Auth bypass not allowed in production")
        return User(oid="dev-user", email="dev@local", display_name="Dev User")

    # Extract token
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]  # Strip "Bearer "

    # Validate
    jwks = await _fetch_jwks(settings.ENTRA_TENANT_ID)
    payload = _decode_token(token, jwks, settings)

    # Extract user info
    oid = payload.get("oid")
    email = payload.get("preferred_username") or payload.get("upn", "")
    display_name = payload.get("name", email)

    if not oid:
        raise HTTPException(status_code=401, detail="Token missing oid claim")

    return User(oid=oid, email=email, display_name=display_name)
