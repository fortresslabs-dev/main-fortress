"""
Chunk Exporter — Writes scraped chunks to S3 AND local disk.

Primary:  S3 bucket (fortress-p1-data-{AccountId}) under prefix scraper-chunks/
Fallback: Local disk /app/chunks/ (dev or if S3 unavailable)

S3 path: s3://fortress-p1-data-{AccountId}/scraper-chunks/{chunk_type}/{YYYY/MM/DD}/{label}_{ts}.json
Local:   /app/chunks/{chunk_type}_{label}_{ts}.json (max_files rotation kept)

Called before every bulk insert so every ingestion cycle is auditable.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_LOCAL_DIR = os.getenv(
    "CHUNK_OUTPUT_DIR",
    "/app/chunks" if Path("/app").exists() else str(Path(__file__).resolve().parents[2] / "chunks"),
)
_S3_BUCKET = os.getenv("S3_BUCKET", "")
_S3_PREFIX = os.getenv("S3_CHUNK_PREFIX", "scraper-chunks")


def _ensure_dir(directory: str) -> Path:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _upload_to_s3(bucket: str, key: str, payload: dict) -> bool:
    """Upload JSON payload to S3. Returns True on success, False on any error."""
    try:
        import boto3
        s3 = boto3.client("s3")
        body = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
        )
        logger.info("chunk_uploaded_s3", bucket=bucket, key=key,
                    count=payload.get("count", 0))
        return True
    except Exception as exc:
        logger.warning("chunk_s3_upload_failed", bucket=bucket, key=key,
                       error=str(exc))
        return False


def export_chunks(
    chunk_type: str,
    chunks: List[Dict[str, Any]],
    label: str = "",
    output_dir: str = _DEFAULT_LOCAL_DIR,
    max_files: int = 5,
    s3_bucket: Optional[str] = None,
) -> Path:
    """
    Write chunks to S3 (primary) and local disk (fallback/dev).

    Args:
        chunk_type:  'compliance_rules' | 'compliance_documents' | 'banking_workflows'
        chunks:      List of dicts to serialize.
        label:       Connector or regulator name suffix.
        output_dir:  Local directory (fallback).
        max_files:   Max local files to keep per type+label (rotation).
        s3_bucket:   Override S3 bucket name (default: S3_BUCKET env var).

    Returns:
        Path of the local file written.
    """
    ts = datetime.now(timezone.utc)
    ts_str = ts.strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    filename_base = f"{chunk_type}{suffix}_{ts_str}.json"

    payload = {
        "exported_at": ts.isoformat(),
        "chunk_type": chunk_type,
        "label": label,
        "count": len(chunks),
        "chunks": chunks,
    }

    # ── S3 upload (primary — ECS production) ─────────────────────────
    bucket = s3_bucket or _S3_BUCKET
    if bucket:
        date_prefix = ts.strftime("%Y/%m/%d")
        s3_key = f"{_S3_PREFIX}/{chunk_type}/{date_prefix}/{filename_base}"
        _upload_to_s3(bucket, s3_key, payload)

    # ── Local write (always — audit trail + dev) ──────────────────────
    out = _ensure_dir(output_dir)
    local_path = out / filename_base

    with open(local_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str, ensure_ascii=False)

    logger.info("chunk_export_written", chunk_type=chunk_type,
                count=len(chunks), file=str(local_path),
                s3=bool(bucket))

    # Rotate local files — keep only most recent max_files
    pattern = f"{chunk_type}{suffix}_*.json"
    existing = sorted(out.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_file in existing[max_files:]:
        try:
            old_file.unlink()
            logger.info("chunk_file_rotated", removed=str(old_file))
        except OSError as exc:
            logger.warning("chunk_file_rotation_failed", file=str(old_file), error=str(exc))

    return local_path
