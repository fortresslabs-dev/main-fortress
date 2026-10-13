"""Orchestrator: snapshot → hash → render → S3 PUT → DB row.

Entrypoint for the exports-worker ECS task. The API hands off via
``POST /exports`` which writes a pending ``export_artifacts`` row in
``pending/`` state; this worker picks it up, gathers the snapshot,
renders, and writes the final WORM-locked object.

CLI:
    python -m src.exports.worker --one-shot <artifact_id>
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import os
import sys
from typing import Any

import structlog
from sqlalchemy import text

from src.exports import canonical, csv_writer, snapshot, storage, vocabulary
from src.exports.signoff import required_signers
from src.services.database import close_db, get_db, init_db


log = structlog.get_logger(__name__)


TEMPLATE_VERSION = "v1.2026.06"


def _document_id(report_type: str, as_of: _dt.date, version_hash: str) -> str:
    """Deterministic doc-ID per design doc §11.2 Q11.

    Format: FTX-<TYPE3>-<YYYY>-<MMDD>-<hash6>"""
    type_codes = {
        "examiner_package": "EVP",
        "ofac_attestation": "OFA",
        "board_report": "BRD",
        "mra_tracker": "MRA",
        "change_log": "CHG",
        "coverage": "COV",
    }
    code = type_codes.get(report_type, report_type[:3].upper())
    return f"FTX-{code}-{as_of.strftime('%Y-%m%d')}-{version_hash[:6]}"


async def build_artifact(
    *,
    bank_id: str,
    report_type: str,
    as_of: _dt.date,
    fmt: str = "pdf",
    bucket: str | None = None,
    kms_key_arn: str | None = None,
    app_commit: str | None = None,
) -> dict[str, Any]:
    bucket = bucket or os.environ["EXPORTS_BUCKET"]
    kms_key_arn = kms_key_arn or os.environ["EXPORTS_KMS_KEY_ARN"]
    app_commit = app_commit or os.environ.get("APP_COMMIT", "unknown")

    async with get_db() as session:
        data = await snapshot.gather(session, report_type=report_type,
                                     bank_id=bank_id, as_of=as_of)
    vocabulary.validate(data)
    sha256_hex, version_hash = canonical.hash_envelope(
        data,
        template_version=TEMPLATE_VERSION,
        vocab_version=vocabulary.CURRENT_VOCAB_VERSION,
        app_commit=app_commit,
    )
    doc_id = _document_id(report_type, as_of, version_hash)

    ctx = {
        **data,
        "report_type": report_type,
        "version_hash": version_hash,
        "vocab_version": vocabulary.CURRENT_VOCAB_VERSION,
        "template_version": TEMPLATE_VERSION,
        "document_id": doc_id,
        "required_signers": list(required_signers(report_type)),
        "is_draft": True,  # set to False after sign-off; sealed re-render
    }

    if fmt == "csv":
        body = csv_writer.csv_for(report_type, data)
    elif fmt == "pdf":
        from src.exports.renderer import render_pdf  # lazy import
        body = await render_pdf(f"{report_type}.html", ctx)
    else:
        raise ValueError(f"unsupported fmt={fmt!r}")

    key = storage.deterministic_key(
        bank_id=bank_id, report_type=report_type, as_of=as_of,
        version_hash=version_hash, fmt=fmt,
    )
    put = storage.put_artifact(
        bucket=bucket, key=key, body=body, kms_key_arn=kms_key_arn,
        metadata={"document_id": doc_id, "sha256_canonical": sha256_hex},
    )
    log.info("artifact_uploaded", bank_id=bank_id, type=report_type,
             as_of=as_of.isoformat(), version_hash=version_hash,
             is_new=put.is_new, key=put.key)
    retain_until = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=1825)
    async with get_db() as session:
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
            "bank": bank_id, "type": report_type, "asof": as_of, "fmt": fmt,
            "vh": version_hash, "bucket": put.bucket, "key": put.key,
            "etag": put.etag, "ver": put.version_id, "kms": kms_key_arn,
            "sz": len(body), "sha": sha256_hex,
            "who": os.environ.get("EXPORT_GENERATED_BY", "exports-worker"),
            "retain": retain_until,
            "scs": sha256_hex,
            "tv": TEMPLATE_VERSION, "vv": vocabulary.CURRENT_VOCAB_VERSION,
            "ac": app_commit,
        })
        await session.commit()
    return {
        "bank_id": bank_id, "type": report_type, "as_of": as_of.isoformat(),
        "version_hash": version_hash, "sha256": sha256_hex,
        "document_id": doc_id, "format": fmt,
        "bucket": put.bucket, "key": put.key,
        "etag": put.etag, "is_new": put.is_new,
    }


async def _one_shot(args):
    await init_db()
    try:
        result = await build_artifact(
            bank_id=args.bank_id,
            report_type=args.type,
            as_of=_dt.date.fromisoformat(args.as_of),
            fmt=args.fmt,
            bucket=args.bucket,
            kms_key_arn=args.kms_key_arn,
        )
        print(result)
    finally:
        await close_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    one = sub.add_parser("one-shot", help="render a single artifact")
    one.add_argument("--bank-id", required=True)
    one.add_argument("--type", required=True,
                     choices=["examiner_package", "ofac_attestation",
                              "board_report", "mra_tracker",
                              "change_log", "coverage"])
    one.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    one.add_argument("--fmt", default="pdf", choices=["pdf", "csv"])
    one.add_argument("--bucket")
    one.add_argument("--kms-key-arn")
    args = parser.parse_args()

    asyncio.run(_one_shot(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
