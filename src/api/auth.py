"""
src/api/auth.py
P2 authentication — Cognito JWT in production, simple token for local dev.

Dashboard users: Cognito hosted UI → JWT → Bearer header
Internal endpoints: PIPELINE2_API_TOKEN env var (local dev / internal)

Roles (from Cognito groups):
  CCO     — full access: generate tests, mark N/A, override, sign-off
  CRO     — read + approve/reject checkpoints + sign-off changes
  CIO     — read only (tech panels)
  Analyst — frontline screening: SDN search, open cases, record decisions
  Admin   — model governance: vector intel + threshold tuning
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

import httpx
import structlog
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = structlog.get_logger(__name__)

_REGION        = os.getenv("AWS_REGION", "us-east-1")
_USER_POOL_ID  = os.getenv("COGNITO_USER_POOL_ID", "")
_CLIENT_ID     = os.getenv("COGNITO_CLIENT_ID", "")
# DEV_MODE gates the plaintext dev-token fallback below. Default false so a
# missing COGNITO_USER_POOL_ID on a deployed stack returns 503 rather than
# silently accepting `Authorization: Bearer <_DEV_TOKEN>`.
_DEV_MODE      = os.getenv("DEV_MODE", "").lower() in ("1", "true", "yes")
_DEV_TOKEN_RAW = os.getenv("PIPELINE2_API_TOKEN", "")
# Only honor the dev token when DEV_MODE is on AND the operator set a non-empty
# token. The historical default of "changeme-token" is intentionally not used
# as a fallback — relying on it would re-introduce the silent-bypass bug.
_DEV_TOKEN     = _DEV_TOKEN_RAW if (_DEV_MODE and _DEV_TOKEN_RAW) else None
_DEV_ROLES     = {"CCO", "CRO", "CIO", "Analyst", "Admin", "admin"}
_JWKS_URL = (
    f"https://cognito-idp.{_REGION}.amazonaws.com"
    f"/{_USER_POOL_ID}/.well-known/jwks.json"
    if _USER_POOL_ID else ""
)

security = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _get_jwks() -> dict:
    if not _JWKS_URL:
        return {}
    try:
        resp = httpx.get(_JWKS_URL, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("cognito_jwks_fetch_failed", error=str(exc))
        return {}


def _decode_cognito_token(token: str) -> dict:
    try:
        from jose import JWTError, jwk, jwt as jose_jwt
    except ImportError:
        raise HTTPException(503, "python-jose not installed — add to requirements.txt")

    jwks = _get_jwks()
    if not jwks:
        raise HTTPException(503, "Cognito JWKS not available")

    try:
        headers = jose_jwt.get_unverified_headers(token)
        kid = headers.get("kid")
        key_data = next(
            (k for k in jwks.get("keys", []) if k["kid"] == kid), None
        )
        if not key_data:
            raise HTTPException(401, "JWT key not found")

        public_key = jwk.construct(key_data)
        claims = jose_jwt.decode(
            token, public_key, algorithms=["RS256"],
            audience=_CLIENT_ID, options={"verify_exp": True},
        )
        expected_iss = (
            f"https://cognito-idp.{_REGION}.amazonaws.com/{_USER_POOL_ID}"
        )
        if claims.get("iss") != expected_iss:
            raise HTTPException(401, "Token issuer invalid")
        return claims
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(401, f"Token validation failed: {exc}")


class CurrentUser:
    def __init__(self, sub: str, email: str, role: str,
                 groups: list, bank_id: str):
        self.sub     = sub
        self.email   = email
        self.role    = role
        self.groups  = groups
        self.bank_id = bank_id

    @property
    def can_edit(self) -> bool:
        return self.role in ("CCO", "Admin", "admin")

    @property
    def can_approve(self) -> bool:
        return self.role in ("CCO", "CRO", "Admin", "admin")

    @property
    def can_generate_tests(self) -> bool:
        return self.role in ("CCO", "Admin", "admin")

    @property
    def can_screen(self) -> bool:
        """SDN search + case decision (Pass/Review/Escalate)."""
        return self.role in ("Analyst", "CCO", "CRO", "Admin", "admin")

    @property
    def can_view_vector_intel(self) -> bool:
        """Vector Intelligence dashboard — Admin write, CRO read-only."""
        return self.role in ("Admin", "CRO", "admin")

    @property
    def can_govern_model(self) -> bool:
        """Threshold tuning + model write actions — Admin only."""
        return self.role in ("Admin", "admin")


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    if not credentials:
        raise HTTPException(401, "Authorization header required")

    token = credentials.credentials

    # Dev / internal token fallback — only honored when DEV_MODE=true AND
    # PIPELINE2_API_TOKEN is set to a non-empty value. Never matched in
    # production deployments.
    if _DEV_TOKEN is not None and token == _DEV_TOKEN:
        # E2E specs vary persona (Analyst / CCO / CRO / CIO / Admin) via
        # the X-E2E-Role header so role-gated endpoints (vector-intel,
        # approvals, screening) can be exercised without a real Cognito
        # session. Unknown values fall back to CCO.
        header_role = request.headers.get("x-e2e-role", "") if request else ""
        role = header_role if header_role in _DEV_ROLES else "CCO"
        return CurrentUser(
            sub="dev", email="dev@local", role=role,
            groups=[role], bank_id=os.getenv("CLIENT_NAME", "devbank"),
        )

    # Production: Cognito JWT. Refuse to serve auth at all without a pool
    # configured — failing closed is the only safe behavior here.
    if not _USER_POOL_ID:
        raise HTTPException(
            503, "COGNITO_USER_POOL_ID not set — add to SSM or ECS env"
        )

    claims = _decode_cognito_token(token)
    groups = claims.get("cognito:groups", [])
    role   = groups[0] if groups else "viewer"
    bank_id = claims.get("custom:bank_id",
                          os.getenv("CLIENT_NAME", ""))
    email = claims.get("email", claims.get("sub", ""))

    logger.info("auth_ok", email=email, role=role, bank_id=bank_id)
    return CurrentUser(
        sub=claims["sub"], email=email,
        role=role, groups=groups, bank_id=bank_id,
    )


def require_cco(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.can_edit:
        raise HTTPException(403, "CCO access required")
    return user


def require_approver(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not user.can_approve:
        raise HTTPException(403, "CCO or CRO access required")
    return user


def require_analyst(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not user.can_screen:
        raise HTTPException(403, "Screening access required (Analyst/CCO/CRO/Admin)")
    return user


def require_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not user.can_govern_model:
        raise HTTPException(403, "Admin access required")
    return user


def require_vector_intel(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not user.can_view_vector_intel:
        raise HTTPException(403, "Vector Intelligence requires Admin or CRO")
    return user
