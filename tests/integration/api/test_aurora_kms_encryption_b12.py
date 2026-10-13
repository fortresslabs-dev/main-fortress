"""
B12 — Aurora / Redis / S3 / Secrets must be encrypted with customer-managed
KMS keys before any pilot bank data lands in the stack.

This test parses `infra/cloudformation/pipeline1-data.yml` and locks every
encryption-at-rest invariant. It runs without AWS credentials in CI — a
pure static check on the template. A separate post-deploy script (TODO,
not in scope here) would call `aws rds describe-db-clusters` to confirm
the deployed cluster matches.

Invariants asserted (regression locks for B12):

  Aurora cluster:
    - StorageEncrypted = true
    - KmsKeyId references a customer-managed key in this stack
      (NOT empty, NOT the AWS-managed `alias/aws/rds`)
    - BackupRetentionPeriod >= 7
    - DeletionProtection = true
    - EnableCloudwatchLogsExports includes 'postgresql'

  Redis replication group:
    - AtRestEncryptionEnabled = true
    - TransitEncryptionEnabled = true
    - TransitEncryptionMode = 'required'
    - KmsKeyId references a customer-managed key in this stack
    - AuthToken is set (not bare TLS)

  S3 bucket:
    - BucketEncryption uses aws:kms with a customer-managed key
    - PublicAccessBlockConfiguration: all four flags true
    - VersioningConfiguration.Status = Enabled

  KMS keys for Aurora / Redis / S3:
    - EnableKeyRotation = true (NIST SP 800-57 — annual rotation)

  Secrets Manager:
    - SecretDbPassword and SecretRedisToken both reference a
      customer-managed KMS key (not the default `alias/aws/secretsmanager`)
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_STACK = REPO_ROOT / "infra" / "cloudformation" / "pipeline1-data.yml"


# ── CFN short-tag loader ────────────────────────────────────────────
# CFN YAML uses !Ref / !Sub / !GetAtt etc. PyYAML doesn't know these tags
# out of the box, so we register a generic constructor that preserves the
# tag + value as a dict like {"Ref": "KmsKeyRds"}. That matches CFN's own
# long-form serialisation and is what the assertions key off below.

class _CfnLoader(yaml.SafeLoader):
    pass


def _cfn_constructor(loader, tag_suffix, node):
    key = f"Fn::{tag_suffix}" if tag_suffix not in {"Ref", "Condition"} else tag_suffix
    if isinstance(node, yaml.ScalarNode):
        return {key: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {key: loader.construct_sequence(node, deep=True)}
    if isinstance(node, yaml.MappingNode):
        return {key: loader.construct_mapping(node, deep=True)}
    raise yaml.constructor.ConstructorError(None, None, f"unhandled node for !{tag_suffix}", node.start_mark)


_CfnLoader.add_multi_constructor("!", _cfn_constructor)


@pytest.fixture(scope="module")
def template() -> dict:
    assert DATA_STACK.exists(), f"{DATA_STACK} not found"
    return yaml.load(DATA_STACK.read_text(), Loader=_CfnLoader)


@pytest.fixture(scope="module")
def resources(template) -> dict:
    return template["Resources"]


# ── Helpers ─────────────────────────────────────────────────────────

def _is_customer_managed_kms_ref(value, resources: dict) -> bool:
    """A KmsKeyId is acceptable if it references a `AWS::KMS::Key` in this
    template (via Ref or GetAtt) — NOT an AWS-managed alias string."""
    if isinstance(value, str):
        # Bare strings are always rejected — they'd be either an alias or
        # the AWS-managed default ("alias/aws/...") which fails the B12 bar.
        return False
    if isinstance(value, dict):
        if "Ref" in value:
            target = value["Ref"]
            return resources.get(target, {}).get("Type") == "AWS::KMS::Key"
        if "Fn::GetAtt" in value:
            target = value["Fn::GetAtt"]
            # GetAtt accepts both "Name.Attr" and ["Name", "Attr"]
            if isinstance(target, list):
                name = target[0]
            else:
                name = target.split(".")[0]
            return resources.get(name, {}).get("Type") == "AWS::KMS::Key"
    return False


# ── Aurora ──────────────────────────────────────────────────────────

def test_b12_aurora_storage_encrypted(resources):
    cluster = resources["AuroraCluster"]
    props = cluster["Properties"]
    assert props.get("StorageEncrypted") is True, (
        "AuroraCluster.StorageEncrypted must be true (B12). "
        "Unencrypted at-rest storage fails bank vendor security review."
    )


def test_b12_aurora_uses_customer_managed_kms(resources):
    cluster = resources["AuroraCluster"]
    kms = cluster["Properties"].get("KmsKeyId")
    assert kms is not None, "AuroraCluster.KmsKeyId is unset — would fall back to alias/aws/rds"
    assert _is_customer_managed_kms_ref(kms, resources), (
        f"AuroraCluster.KmsKeyId must reference an AWS::KMS::Key in this "
        f"template (got {kms!r}). AWS-managed keys can't be rotated on a "
        f"schedule we control or shared across accounts for restores."
    )


def test_b12_aurora_backup_retention_meets_floor(resources):
    cluster = resources["AuroraCluster"]
    retention = cluster["Properties"].get("BackupRetentionPeriod", 0)
    assert retention >= 7, (
        f"AuroraCluster.BackupRetentionPeriod = {retention} — must be ≥ 7 "
        f"days so a week-old PITR is always available for incident response."
    )


def test_b12_aurora_deletion_protection_on(resources):
    cluster = resources["AuroraCluster"]
    assert cluster["Properties"].get("DeletionProtection") is True, (
        "AuroraCluster.DeletionProtection must be true — a stack `delete` "
        "or rename would otherwise drop pilot data."
    )


def test_b12_aurora_exports_postgres_logs(resources):
    cluster = resources["AuroraCluster"]
    exports = cluster["Properties"].get("EnableCloudwatchLogsExports", [])
    assert "postgresql" in exports, (
        f"AuroraCluster.EnableCloudwatchLogsExports must include 'postgresql' "
        f"so audit + slow-query logs land in CloudWatch (got {exports!r})."
    )


# ── Redis ──────────────────────────────────────────────────────────

def test_b12_redis_at_rest_encryption(resources):
    redis = resources["RedisCluster"]
    assert redis["Properties"].get("AtRestEncryptionEnabled") is True, (
        "RedisCluster.AtRestEncryptionEnabled must be true (B12)."
    )


def test_b12_redis_transit_encryption_required(resources):
    redis = resources["RedisCluster"]["Properties"]
    assert redis.get("TransitEncryptionEnabled") is True, (
        "RedisCluster.TransitEncryptionEnabled must be true."
    )
    assert redis.get("TransitEncryptionMode") == "required", (
        f"RedisCluster.TransitEncryptionMode must be 'required' "
        f"(got {redis.get('TransitEncryptionMode')!r}). 'preferred' allows "
        f"plaintext fallback."
    )


def test_b12_redis_uses_customer_managed_kms(resources):
    redis = resources["RedisCluster"]
    kms = redis["Properties"].get("KmsKeyId")
    assert kms is not None, "RedisCluster.KmsKeyId is unset"
    assert _is_customer_managed_kms_ref(kms, resources), (
        f"RedisCluster.KmsKeyId must reference a CMK in this template "
        f"(got {kms!r})."
    )


def test_b12_redis_auth_token_set(resources):
    redis = resources["RedisCluster"]
    assert redis["Properties"].get("AuthToken") is not None, (
        "RedisCluster.AuthToken must be set — TLS without auth still lets "
        "anything in the SG connect."
    )


# ── S3 ─────────────────────────────────────────────────────────────

def test_b12_s3_bucket_kms_encrypted(resources):
    bucket = resources["S3BucketP1"]
    enc = bucket["Properties"].get("BucketEncryption")
    assert enc is not None, "S3BucketP1.BucketEncryption is unset"

    configs = enc.get("ServerSideEncryptionConfiguration", [])
    assert configs, "S3BucketP1 has no ServerSideEncryptionConfiguration entries"

    default = configs[0].get("ServerSideEncryptionByDefault", {})
    assert default.get("SSEAlgorithm") == "aws:kms", (
        f"S3BucketP1 SSE algorithm must be 'aws:kms' "
        f"(got {default.get('SSEAlgorithm')!r}). AES256 with bucket keys is "
        f"insufficient for the bank-vendor encryption matrix."
    )
    assert _is_customer_managed_kms_ref(default.get("KMSMasterKeyID"), resources), (
        "S3BucketP1 KMSMasterKeyID must reference a CMK in this template."
    )


def test_b12_s3_public_access_fully_blocked(resources):
    bucket = resources["S3BucketP1"]
    pab = bucket["Properties"].get("PublicAccessBlockConfiguration", {})
    for flag in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"):
        assert pab.get(flag) is True, (
            f"S3BucketP1.PublicAccessBlockConfiguration.{flag} must be true "
            f"(got {pab.get(flag)!r})."
        )


def test_b12_s3_versioning_enabled(resources):
    bucket = resources["S3BucketP1"]
    versioning = bucket["Properties"].get("VersioningConfiguration", {})
    assert versioning.get("Status") == "Enabled", (
        "S3BucketP1.VersioningConfiguration.Status must be 'Enabled' so a "
        "ransomware overwrite is recoverable."
    )


# ── KMS key rotation ───────────────────────────────────────────────

@pytest.mark.parametrize("key_name", ["KmsKeyRds", "KmsKeyRedis", "KmsKeyS3"])
def test_b12_kms_keys_rotate_annually(resources, key_name):
    key = resources[key_name]
    assert key["Type"] == "AWS::KMS::Key", f"{key_name} is not a KMS::Key"
    assert key["Properties"].get("EnableKeyRotation") is True, (
        f"{key_name}.EnableKeyRotation must be true — NIST SP 800-57 calls "
        f"for annual rotation of symmetric DEKs."
    )


# ── Secrets Manager ────────────────────────────────────────────────

@pytest.mark.parametrize("secret_name", ["SecretDbPassword", "SecretRedisToken"])
def test_b12_secrets_use_customer_managed_kms(resources, secret_name):
    secret = resources[secret_name]
    kms = secret["Properties"].get("KmsKeyId")
    assert kms is not None, (
        f"{secret_name}.KmsKeyId is unset — would fall back to "
        f"alias/aws/secretsmanager (no cross-account share, no rotation control)."
    )
    assert _is_customer_managed_kms_ref(kms, resources), (
        f"{secret_name}.KmsKeyId must reference a CMK in this template "
        f"(got {kms!r})."
    )
