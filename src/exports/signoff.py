"""Sign-off + access-log writes (design doc §7 + §9 Q9/Q10).

POST /exports/{id}/sign needs to:
  1. Verify the signer has the role required by policy.yaml.
  2. Verify the signing strength matches (strong = typed name + ack).
  3. Insert an export_signoffs row; once the required-signers set is
     covered, the artifact becomes downloadable.

GET /exports/{id}/download writes an export_access_log row regardless
of outcome (success or 4xx).
"""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass
from typing import Sequence

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_POLICY_PATH = os.path.join(os.path.dirname(__file__), "policy.yaml")


@dataclass(frozen=True)
class SignerPolicy:
    required_signers: tuple[str, ...]
    signing_strength: str  # "strong" or "jti_only"


def _load_policy() -> dict[str, SignerPolicy]:
    with open(_POLICY_PATH) as f:
        data = yaml.safe_load(f)
    return {
        rtype: SignerPolicy(
            required_signers=tuple(spec["required_signers"]),
            signing_strength=spec["signing_strength"],
        )
        for rtype, spec in data["signers"].items()
    }


_POLICY: dict[str, SignerPolicy] = _load_policy()


class SignoffError(Exception):
    """Raised for any policy violation (wrong role, missing fields)."""


async def record_signoff(
    session: AsyncSession,
    *,
    artifact_id: str,
    report_type: str,
    signer_person_id: str,
    signer_role: str,
    signature_method: str,
    ip_address: str | None,
    user_agent: str | None,
    jwt_jti: str | None,
    typed_full_name: str | None = None,
    acknowledgement: str | None = None,
) -> None:
    policy = _POLICY.get(report_type)
    if policy is None:
        raise SignoffError(f"no signer policy for report_type={report_type!r}")
    if signer_role not in policy.required_signers:
        raise SignoffError(
            f"role {signer_role!r} not required for {report_type}; "
            f"required: {policy.required_signers}"
        )
    if policy.signing_strength == "strong" and not (typed_full_name and acknowledgement):
        raise SignoffError(
            f"{report_type} requires strong signing — typed_full_name + acknowledgement"
        )

    await session.execute(text("""
        INSERT INTO export_signoffs (
            artifact_id, signer_person_id, signer_role, signed_at,
            signature_method, ip_address, user_agent, jwt_jti,
            typed_full_name, acknowledgement
        ) VALUES (
            :artifact_id, :signer_person_id, :signer_role, now(),
            CAST(:signature_method AS signature_method_t), :ip, :ua, :jti,
            :name, :ack
        )
        ON CONFLICT (artifact_id, signer_person_id, signer_role) DO NOTHING
    """), {
        "artifact_id": artifact_id,
        "signer_person_id": signer_person_id,
        "signer_role": signer_role,
        "signature_method": signature_method,
        "ip": ip_address,
        "ua": user_agent,
        "jti": jwt_jti,
        "name": typed_full_name,
        "ack": acknowledgement,
    })


async def signatures_complete(
    session: AsyncSession, *, artifact_id: str, report_type: str
) -> bool:
    """Has every required signer signed?"""
    policy = _POLICY.get(report_type)
    if policy is None:
        return False
    rows = (await session.execute(text(
        "SELECT signer_role FROM export_signoffs WHERE artifact_id = :a"
    ), {"a": artifact_id})).all()
    signed = {r.signer_role for r in rows}
    return set(policy.required_signers).issubset(signed)


async def record_access(
    session: AsyncSession,
    *,
    artifact_id: str,
    accessed_by: str,
    ip_address: str | None,
    jwt_jti: str | None,
    http_status: int,
) -> None:
    await session.execute(text("""
        INSERT INTO export_access_log (
            artifact_id, accessed_by, accessed_at, ip_address, jwt_jti, http_status
        ) VALUES (:a, :who, now(), :ip, :jti, :status)
    """), {"a": artifact_id, "who": accessed_by, "ip": ip_address,
           "jti": jwt_jti, "status": http_status})


def required_signers(report_type: str) -> Sequence[str]:
    return _POLICY[report_type].required_signers
