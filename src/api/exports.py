"""Exports API router — new contract per design doc §4 + §7.

Replaces the stateless-token model in ``src/api/dashboard_api.py`` with
persisted, WORM-locked ``export_artifacts`` rows. The legacy endpoints
remain (for back-compat) until the dashboard is fully migrated.

Endpoints:
  POST  /api/v1/exports                    — snapshot+hash+S3 PUT-if-absent
  GET   /api/v1/exports                    — list past artifacts for a bank
  GET   /api/v1/exports/{artifact_id}      — artifact metadata
  POST  /api/v1/exports/{artifact_id}/sign — record sign-off
  GET   /api/v1/exports/{artifact_id}/download — S3 GET + access log

PDF rendering is deferred to the exports-worker (ECS RunTask). The API
process never imports Playwright. For CSV (cheap, no Chromium) we can
build inline; the API immediately returns the artifact metadata.
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Optional

import boto3
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from src.api.auth import CurrentUser, require_approver
from src.exports import canonical, csv_writer, snapshot, storage, vocabulary
from src.exports.signoff import (
    record_access, record_signoff, required_signers, signatures_complete,
)
from src.exports.worker import TEMPLATE_VERSION, _document_id, build_artifact
from src.services.database import get_db


logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/exports", tags=["Exports"])


_REPORT_TYPES = {"examiner_package", "ofac_attestation", "board_report",
                 "mra_tracker", "change_log", "coverage"}


class CreateExportRequest(BaseModel):
    bank_id: str
    type: str
    as_of: Optional[str] = None    # YYYY-MM-DD; default = today
    format: str = "pdf"


class SignoffRequest(BaseModel):
    signer_person_id: str
    signer_role: str
    signature_method: str = "jwt_jti"   # or "sso_session" / "manual_upload"
    typed_full_name: Optional[str] = None
    acknowledgement: Optional[str] = None


def _bucket_for(bank_id: str) -> str:
    env = os.getenv("ENVIRONMENT", "production")
    return f"fortress-p2-exports-{bank_id}-{env}"


def _kms_for(bank_id: str) -> str:
    # Set per-bank via SSM at task launch; the CFN export-bucket stack
    # creates an alias.
    return os.environ["EXPORTS_KMS_KEY_ARN"]


@router.post("")
async def create_export(
    body: CreateExportRequest,
    user: CurrentUser = Depends(require_approver),
):
    if body.type not in _REPORT_TYPES:
        raise HTTPException(400, f"type must be one of {sorted(_REPORT_TYPES)}")
    if body.format not in ("pdf", "csv"):
        raise HTTPException(400, "format must be pdf|csv")

    try:
        as_of = (_dt.date.fromisoformat(body.as_of) if body.as_of
                 else _dt.date.today())
    except ValueError:
        raise HTTPException(400, "as_of must be ISO-8601 (YYYY-MM-DD)")

    if body.format == "csv":
        # CSVs are cheap to render — build inline so the API can return
        # the artifact in one round-trip.
        async with get_db() as session:
            data = await snapshot.gather(session, report_type=body.type,
                                         bank_id=body.bank_id, as_of=as_of)
        vocabulary.validate(data)
        sha256_hex, vh = canonical.hash_envelope(
            data, template_version=TEMPLATE_VERSION,
            vocab_version=vocabulary.CURRENT_VOCAB_VERSION,
            app_commit=os.getenv("APP_COMMIT", "unknown"),
        )
        doc_id = _document_id(body.type, as_of, vh)
        csv_bytes = csv_writer.csv_for(body.type, data)
        key = storage.deterministic_key(
            bank_id=body.bank_id, report_type=body.type,
            as_of=as_of, version_hash=vh, fmt="csv",
        )
        put = storage.put_artifact(
            bucket=_bucket_for(body.bank_id),
            key=key, body=csv_bytes,
            kms_key_arn=_kms_for(body.bank_id),
            metadata={"document_id": doc_id, "sha256_canonical": sha256_hex},
        )
        async with get_db() as session:
            await _upsert_artifact_row(
                session, bank_id=body.bank_id, type_=body.type, as_of=as_of,
                fmt="csv", version_hash=vh, sha256=sha256_hex,
                snapshot_canonical_sha=sha256_hex, doc_id=doc_id,
                bucket=put.bucket, key=put.key, etag=put.etag,
                version_id=put.version_id, kms=_kms_for(body.bank_id),
                byte_size=len(csv_bytes), generated_by=user.email,
            )
        return {"artifact_id": None, "document_id": doc_id, "version_hash": vh,
                "as_of": as_of.isoformat(), "format": "csv",
                "bucket": put.bucket, "key": put.key, "is_new": put.is_new,
                "status": "ready"}

    # PDF — hand off to exports-worker ECS RunTask. Worker writes both
    # the artifact and the export_artifacts row on completion.
    cluster = os.getenv("EXPORTS_WORKER_CLUSTER",
                        f"fortress-pipeline2-{body.bank_id}-production")
    td = os.getenv("EXPORTS_WORKER_TD",
                   f"fortress-p2-exports-worker-{body.bank_id}")
    subnets = os.environ["EXPORTS_WORKER_SUBNETS"].split(",")
    sgs = os.environ["EXPORTS_WORKER_SGS"].split(",")

    ecs = boto3.client("ecs", region_name=os.getenv("AWS_REGION", "us-east-1"))
    resp = ecs.run_task(
        cluster=cluster,
        taskDefinition=td,
        launchType="FARGATE",
        networkConfiguration={"awsvpcConfiguration": {
            "subnets": subnets, "securityGroups": sgs,
            "assignPublicIp": "DISABLED",
        }},
        overrides={"containerOverrides": [{
            "name": "exports-worker",
            "command": ["python", "-m", "src.exports.worker", "one-shot",
                        "--bank-id", body.bank_id, "--type", body.type,
                        "--as-of", as_of.isoformat(), "--fmt", "pdf"],
            "environment": [
                {"name": "EXPORT_GENERATED_BY", "value": user.email},
            ],
        }]},
    )
    task_arn = resp["tasks"][0]["taskArn"] if resp.get("tasks") else None
    logger.info("exports_worker_dispatched", bank_id=body.bank_id,
                type=body.type, as_of=as_of.isoformat(), task_arn=task_arn,
                by=user.email)
    return {"artifact_id": None, "status": "rendering", "task_arn": task_arn,
            "as_of": as_of.isoformat(), "format": "pdf",
            "poll_url": f"/api/v1/exports?bank_id={body.bank_id}"
                        f"&type={body.type}&as_of={as_of.isoformat()}"}


async def _upsert_artifact_row(session, *, bank_id, type_, as_of, fmt,
                               version_hash, sha256, snapshot_canonical_sha,
                               doc_id, bucket, key, etag, version_id, kms,
                               byte_size, generated_by) -> None:
    retain_until = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=1825)
    await session.execute(text("""
        INSERT INTO export_artifacts (
            bank_id, type, as_of, format, version_hash, s3_bucket, s3_key,
            s3_etag, s3_version_id, kms_key_arn, byte_size, sha256,
            generated_by, generated_at, object_lock_retain_until,
            snapshot_canonical_sha256, template_version, vocab_version, app_commit
        ) VALUES (
            :bank, :type, :asof, :fmt, :vh, :bucket, :key,
            :etag, :ver, :kms, :sz, :sha,
            :who, now(), :retain,
            :scs, :tv, :vv, :ac
        )
        ON CONFLICT (bank_id, type, as_of, version_hash) DO NOTHING
    """), {
        "bank": bank_id, "type": type_, "asof": as_of, "fmt": fmt,
        "vh": version_hash, "bucket": bucket, "key": key, "etag": etag,
        "ver": version_id, "kms": kms, "sz": byte_size, "sha": sha256,
        "who": generated_by, "retain": retain_until,
        "scs": snapshot_canonical_sha,
        "tv": TEMPLATE_VERSION, "vv": vocabulary.CURRENT_VOCAB_VERSION,
        "ac": os.getenv("APP_COMMIT", "unknown"),
    })


@router.get("")
async def list_exports(bank_id: str, type: Optional[str] = None,
                       as_of: Optional[str] = None,
                       user: CurrentUser = Depends(require_approver)):
    where = "WHERE bank_id = :b"
    params = {"b": bank_id}
    if type:
        where += " AND type = :t"
        params["t"] = type
    if as_of:
        where += " AND as_of = :a"
        params["a"] = _dt.date.fromisoformat(as_of)
    async with get_db() as session:
        rows = (await session.execute(text(f"""
            SELECT artifact_id, type, as_of, format, version_hash,
                   sha256, generated_by, generated_at,
                   object_lock_retain_until
            FROM export_artifacts {where}
            ORDER BY generated_at DESC LIMIT 200
        """), params)).mappings().all()
    return {"artifacts": [dict(r) for r in rows]}


@router.get("/{artifact_id}")
async def get_artifact(artifact_id: str,
                       user: CurrentUser = Depends(require_approver)):
    async with get_db() as session:
        row = (await session.execute(text("""
            SELECT * FROM export_artifacts WHERE artifact_id = :a
        """), {"a": artifact_id})).mappings().first()
        if not row:
            raise HTTPException(404, "artifact not found")
        signoffs = (await session.execute(text("""
            SELECT signer_role, signed_at FROM export_signoffs
            WHERE artifact_id = :a
        """), {"a": artifact_id})).all()
        complete = await signatures_complete(
            session, artifact_id=artifact_id, report_type=row.type)
    return {
        **dict(row),
        "signoffs": [{"role": s.signer_role,
                      "signed_at": s.signed_at.isoformat()} for s in signoffs],
        "required_signers": list(required_signers(row.type)),
        "fully_signed": complete,
    }


@router.post("/{artifact_id}/sign")
async def sign_artifact(artifact_id: str, body: SignoffRequest,
                        request: Request,
                        user: CurrentUser = Depends(require_approver)):
    async with get_db() as session:
        row = (await session.execute(text(
            "SELECT type FROM export_artifacts WHERE artifact_id = :a"
        ), {"a": artifact_id})).first()
        if not row:
            raise HTTPException(404, "artifact not found")
        try:
            await record_signoff(
                session,
                artifact_id=artifact_id, report_type=row.type,
                signer_person_id=body.signer_person_id,
                signer_role=body.signer_role,
                signature_method=body.signature_method,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                jwt_jti=getattr(user, "jti", None),
                typed_full_name=body.typed_full_name,
                acknowledgement=body.acknowledgement,
            )
            complete = await signatures_complete(
                session, artifact_id=artifact_id, report_type=row.type)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise HTTPException(400, str(exc))
    return {"artifact_id": artifact_id, "fully_signed": complete}


@router.get("/{artifact_id}/download")
async def download_artifact(artifact_id: str, request: Request,
                            user: CurrentUser = Depends(require_approver)):
    async with get_db() as session:
        row = (await session.execute(text("""
            SELECT type, format, s3_bucket, s3_key FROM export_artifacts
            WHERE artifact_id = :a
        """), {"a": artifact_id})).first()
        if not row:
            raise HTTPException(404, "artifact not found")
        # Sign-off gate per Q9 (dual-signer artifacts watermark until signed).
        complete = await signatures_complete(
            session, artifact_id=artifact_id, report_type=row.type)
        status = 200 if complete else 200  # downloadable either way; UI marks DRAFT
        await record_access(
            session, artifact_id=artifact_id, accessed_by=user.email,
            ip_address=request.client.host if request.client else None,
            jwt_jti=getattr(user, "jti", None), http_status=status,
        )
        await session.commit()

    body = storage.get_artifact(bucket=row.s3_bucket, key=row.s3_key)
    media_type = "application/pdf" if row.format == "pdf" else "text/csv"

    def _stream():
        yield body

    return StreamingResponse(_stream(), media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{row.s3_key.split("/")[-1]}"',
        "X-Fully-Signed": "true" if complete else "false",
    })
