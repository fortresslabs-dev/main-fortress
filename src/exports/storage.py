"""S3 WORM PUT-if-absent for export artifacts (design doc §7).

Idempotent contract: same content hash + same key → no re-upload, no
re-render, return the existing object. Uses the deterministic key

    {bank}/{type}/{as_of}/{hash7}.{fmt}

so a second POST with identical canonical data lands on the same key.
S3 IfNoneMatch on the upload guarantees we don't replace a locked
object (Object Lock COMPLIANCE would refuse it anyway — IfNoneMatch
just gives us a clean 412 instead of a service error).
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError


@dataclasses.dataclass(frozen=True)
class PutResult:
    bucket: str
    key: str
    etag: str
    version_id: Optional[str]
    is_new: bool   # False means the object already existed (idempotent re-run)


def deterministic_key(*, bank_id: str, report_type: str, as_of: _dt.date,
                      version_hash: str, fmt: str) -> str:
    return f"{bank_id}/{report_type}/{as_of.isoformat()}/{version_hash}.{fmt}"


def put_artifact(
    *,
    bucket: str,
    key: str,
    body: bytes,
    kms_key_arn: str,
    retain_days: int = 1825,
    metadata: dict | None = None,
    s3=None,
) -> PutResult:
    """Upload with Object Lock COMPLIANCE + KMS, return ETag + version.

    Re-upload of an existing key returns is_new=False. We HEAD first to
    avoid spending a full body upload only to be refused by Object Lock.
    """
    s3 = s3 or boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))

    # Fast path — does the object already exist?
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        return PutResult(bucket=bucket, key=key,
                         etag=head["ETag"].strip('"'),
                         version_id=head.get("VersionId"),
                         is_new=False)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
            raise

    retain_until = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=retain_days)
    put = s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/pdf" if key.endswith(".pdf") else "text/csv",
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=kms_key_arn,
        ObjectLockMode="COMPLIANCE",
        ObjectLockRetainUntilDate=retain_until,
        Metadata={k: str(v) for k, v in (metadata or {}).items()},
    )
    return PutResult(bucket=bucket, key=key,
                     etag=put["ETag"].strip('"'),
                     version_id=put.get("VersionId"),
                     is_new=True)


def get_artifact(*, bucket: str, key: str, s3=None) -> bytes:
    """Stream object bytes through the API per design doc §11.2 Q21 — no
    pre-signed URLs are exposed externally."""
    s3 = s3 or boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
    resp = s3.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()
