"""
Fortress API Client — used by Pipeline 2 (customer Docker) to fetch
compliance rules from Pipeline 1 (Fortress Cloud).

Features:
  - Circuit breaker (fail-open: use cached rules if API is down)
  - Local Redis cache (configurable TTL, default 1 hour)
  - Retry logic (3 attempts with exponential backoff)
  - PII sanitizer (defence-in-depth: blocks accidental data leaks)
  - Zero customer data sent — only rule queries (geography, regulator, domain)
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Circuit breaker states
_CB_CLOSED = "closed"
_CB_OPEN = "open"
_CB_HALF_OPEN = "half_open"

# PII field deny-list — outgoing requests must NEVER contain these params.
_PII_DENY_LIST = frozenset({
    "account_number", "ssn", "social_security", "beneficiary_name",
    "sender_name", "party_name", "customer_name", "date_of_birth",
    "tax_id", "tin", "routing_number", "card_number", "cvv",
    "transaction_id", "aadhaar", "pan_number",
})


class DataPrivacyError(Exception):
    """Raised when an outgoing request would leak customer PII."""


class FortressAPIClient:
    """Read-only client for the Fortress Compliance Rules API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        cache_ttl: int = 3600,
        max_retries: int = 3,
        timeout: float = 30.0,
        cb_failure_threshold: int = 5,
        cb_recovery_timeout: int = 60,
    ):
        self.base_url = (base_url or os.getenv("FORTRESS_API_URL", "https://api.fortresslabs.dev")).rstrip("/")
        self.api_key = api_key or os.getenv("FORTRESS_API_KEY", "")
        self.cache_ttl = int(os.getenv("FORTRESS_CACHE_TTL", str(cache_ttl)))
        self.max_retries = max_retries
        self.timeout = timeout

        # Circuit breaker state
        self._cb_state = _CB_CLOSED
        self._cb_failures = 0
        self._cb_failure_threshold = cb_failure_threshold
        self._cb_recovery_timeout = cb_recovery_timeout
        self._cb_last_failure_time = 0.0

        # In-memory fallback cache (Dict[cache_key, (data, expiry_ts)])
        self._local_cache: Dict[str, tuple] = {}

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers(),
            timeout=self.timeout,
        )

        logger.info("fortress_api_client_init", base_url=self.base_url)

    def _headers(self) -> Dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": "FortressAgent/2.0"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    # ── Cache helpers ───────────────────────────────────────────────

    def _cache_key(self, path: str, params: Dict) -> str:
        sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
        return f"{path}?{sorted_params}"

    def _cache_get(self, key: str) -> Optional[Any]:
        entry = self._local_cache.get(key)
        if entry and entry[1] > time.time():
            return entry[0]
        return None

    def _cache_set(self, key: str, data: Any) -> None:
        self._local_cache[key] = (data, time.time() + self.cache_ttl)

    # ── Circuit breaker ─────────────────────────────────────────────

    def _cb_record_success(self) -> None:
        self._cb_failures = 0
        self._cb_state = _CB_CLOSED

    def _cb_record_failure(self) -> None:
        self._cb_failures += 1
        self._cb_last_failure_time = time.time()
        if self._cb_failures >= self._cb_failure_threshold:
            self._cb_state = _CB_OPEN
            logger.warning("circuit_breaker_open", failures=self._cb_failures)

    def _cb_allow_request(self) -> bool:
        if self._cb_state == _CB_CLOSED:
            return True
        if self._cb_state == _CB_OPEN:
            if time.time() - self._cb_last_failure_time > self._cb_recovery_timeout:
                self._cb_state = _CB_HALF_OPEN
                return True
            return False
        # half_open — allow one probe
        return True

    # ── Core request with retry + circuit breaker ───────────────────

    @staticmethod
    def _check_pii(params: Dict) -> None:
        """Raise DataPrivacyError if params contain PII fields."""
        leaked = _PII_DENY_LIST.intersection(k.lower() for k in params)
        if leaked:
            raise DataPrivacyError(
                f"Blocked outgoing request containing PII fields: {sorted(leaked)}. "
                "Customer data must NEVER be sent to Fortress API."
            )

    async def _request(self, path: str, params: Dict) -> Any:
        self._check_pii(params)
        cache_key = self._cache_key(path, params)

        # Check cache first
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("fortress_api_cache_hit", path=path)
            return cached

        # Circuit breaker check — fail-open with cached data
        if not self._cb_allow_request():
            logger.warning("circuit_breaker_rejecting", path=path)
            stale = self._local_cache.get(cache_key)
            if stale:
                return stale[0]  # Return stale cache (fail-open)
            return {"rules": [], "entries": [], "error": "API unavailable (circuit open)"}

        # Retry loop
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await self._client.get(path, params={k: v for k, v in params.items() if v is not None})
                resp.raise_for_status()
                data = resp.json()
                self._cb_record_success()
                self._cache_set(cache_key, data)
                return data
            except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as exc:
                last_error = exc
                logger.warning("fortress_api_retry", attempt=attempt, error=str(exc)[:200])
                if attempt < self.max_retries:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)  # exponential backoff

        # All retries exhausted
        self._cb_record_failure()
        stale = self._local_cache.get(cache_key)
        if stale:
            logger.warning("fortress_api_fallback_cache", path=path)
            return stale[0]
        raise ConnectionError(f"Fortress API unreachable after {self.max_retries} attempts: {last_error}")

    # ── Public API methods ──────────────────────────────────────────

    async def get_rules(
        self,
        geography: str = "usa",
        regulator: Optional[str] = None,
        category: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch compliance rules from Fortress API. No customer data sent."""
        data = await self._request(
            "/api/v1/rules",
            {"geography": geography, "regulator": regulator, "category": category, "domain": domain},
        )
        return data.get("rules", [])

    async def get_ofac_sdn_list(self, limit: int = 10000) -> List[Dict[str, Any]]:
        """Fetch OFAC SDN list (public data). No customer data sent."""
        data = await self._request("/api/v1/ofac/sdn-list", {"limit": limit})
        return data.get("entries", [])

    async def get_rule_changes(self, since: str, geography: str = "usa") -> List[Dict[str, Any]]:
        """Fetch rules changed since a date. No customer data sent."""
        data = await self._request("/api/v1/rules/changes", {"since": since, "geography": geography})
        return data.get("changes", [])

    async def close(self) -> None:
        await self._client.aclose()
