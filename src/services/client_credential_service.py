"""
ClientCredentialService — per-bank API key lifecycle.

Keys are generated as cryptographically random 32-byte secrets,
encoded as hex (64 chars). Only the SHA-256 hash is stored in the
database — the raw key is shown once at provisioning time and never
stored by Fortress.

Validation: incoming requests provide the raw key in X-Api-Key header.
We SHA-256 hash it and compare against the stored hash (constant-time).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from typing import Optional

import structlog
from sqlalchemy import text

logger = structlog.get_logger(__name__)


class ClientCredentialService:

    # ── Provisioning ────────────────────────────────────────────────

    async def provision(
        self,
        session,
        customer_id: str,
        bank_name: str,
        tier: str = "standard",
    ) -> str:
        """
        Register a new bank. Returns the raw API key — shown once only.
        Raises ValueError if customer_id already exists.
        """
        existing = await session.execute(
            text("SELECT id FROM client_credentials WHERE customer_id = :cid"),
            {"cid": customer_id}
        )
        if existing.fetchone():
            raise ValueError(f"customer_id '{customer_id}' already registered")

        raw_key = secrets.token_hex(32)
        key_hash = self._hash(raw_key)

        await session.execute(text("""
            INSERT INTO client_credentials
                (id, customer_id, bank_name, api_key_hash, is_active, tier, created_at)
            VALUES
                (:id, :cid, :name, :hash, true, :tier, now())
        """), {
            "id": str(uuid.uuid4()),
            "cid": customer_id,
            "name": bank_name,
            "hash": key_hash,
            "tier": tier,
        })
        await session.commit()

        logger.info("client_provisioned",
                    customer_id=customer_id, bank_name=bank_name, tier=tier)
        # raw_key shown once — caller must send to bank securely, never log it
        return raw_key

    # ── Validation (called on every sync request) ────────────────────

    async def validate(
        self,
        session,
        customer_id: str,
        raw_key: str,
    ) -> bool:
        """
        Validate incoming api_key against stored hash.
        Constant-time comparison prevents timing attacks.
        """
        row = await session.execute(text("""
            SELECT api_key_hash, is_active
            FROM client_credentials
            WHERE customer_id = :cid
        """), {"cid": customer_id})
        record = row.fetchone()

        if not record:
            logger.warning("auth_unknown_customer", customer_id=customer_id)
            return False

        if not record.is_active:
            logger.warning("auth_inactive_customer", customer_id=customer_id)
            return False

        incoming_hash = self._hash(raw_key)
        valid = hmac.compare_digest(
            incoming_hash.encode(),
            record.api_key_hash.encode()
        )

        if valid:
            try:
                await session.execute(text("""
                    UPDATE client_credentials SET last_seen_at = now()
                    WHERE customer_id = :cid
                """), {"cid": customer_id})
                await session.commit()
            except Exception:
                pass
        else:
            logger.warning("auth_bad_key", customer_id=customer_id)

        return valid

    # ── Key rotation ─────────────────────────────────────────────────

    async def rotate(self, session, customer_id: str) -> str:
        """Issue new API key. Old key immediately invalid."""
        row = await session.execute(text("""
            SELECT id FROM client_credentials
            WHERE customer_id = :cid AND is_active = true
        """), {"cid": customer_id})
        if not row.fetchone():
            raise ValueError(f"No active credentials for '{customer_id}'")

        new_raw = secrets.token_hex(32)
        await session.execute(text("""
            UPDATE client_credentials
            SET api_key_hash = :hash, last_seen_at = null
            WHERE customer_id = :cid
        """), {"hash": self._hash(new_raw), "cid": customer_id})
        await session.commit()
        logger.info("client_key_rotated", customer_id=customer_id)
        return new_raw

    # ── Suspend / reactivate ─────────────────────────────────────────

    async def set_active(self, session, customer_id: str, active: bool) -> None:
        await session.execute(text("""
            UPDATE client_credentials SET is_active = :active
            WHERE customer_id = :cid
        """), {"active": active, "cid": customer_id})
        await session.commit()
        logger.info("client_status_changed",
                    customer_id=customer_id, active=active)

    # ── List / lookup / status (admin API surface, H3) ───────────────

    _ADMIN_COLS = (
        "customer_id, bank_name, tier, is_active, created_at, last_seen_at"
    )

    async def list_all(self, session) -> list:
        rows = await session.execute(text(f"""
            SELECT {self._ADMIN_COLS}
            FROM client_credentials ORDER BY created_at DESC
        """))
        return rows.fetchall()

    async def list_clients(self, session) -> list:
        """Alias for list_all — name client_admin_api.py imports."""
        return await self.list_all(session)

    async def get_client(self, session, customer_id: str):
        """Return one client row by customer_id, or None.

        Used by GET /internal/admin/clients/{customer_id} and the PATCH
        handler to confirm existence before mutating. None → 404 at the
        route layer.
        """
        row = await session.execute(text(f"""
            SELECT {self._ADMIN_COLS}
            FROM client_credentials
            WHERE customer_id = :cid
        """), {"cid": customer_id})
        return row.fetchone()

    async def update_status(
        self, session, customer_id: str, is_active: bool
    ) -> bool:
        """Toggle is_active. Returns True if a row was updated, False if not
        found. Distinct from `set_active` (which silently no-ops on missing)
        so the admin route can map False → 404."""
        result = await session.execute(text("""
            UPDATE client_credentials SET is_active = :active
            WHERE customer_id = :cid
        """), {"active": is_active, "cid": customer_id})
        await session.commit()
        updated = (result.rowcount or 0) > 0
        if updated:
            logger.info("client_status_changed",
                        customer_id=customer_id, active=is_active)
        return updated

    async def rotate_key(self, session, customer_id: str) -> str:
        """Alias for rotate — name client_admin_api.py imports."""
        return await self.rotate(session, customer_id)

    @staticmethod
    def _hash(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    # ── Backward-compat alias ────────────────────────────────────────
    async def verify(
        self,
        session,
        customer_id: str,
        raw_key: str,
    ) -> bool:
        """Alias for validate() — used by sync_api.py."""
        return await self.validate(session, customer_id, raw_key)
