"""
src/services/opensearch_client.py

DEPRECATED in pilot — OpenSearch Serverless was dropped from the budget.
Aurora pgvector + tsvector is the sole vector + BM25 store. This module is
kept only so legacy imports do not break; all functions short-circuit when
OPENSEARCH_ENDPOINT is unset (the production task definitions do not set it).

The scraper gates calls behind ``os.environ.get("OPENSEARCH_ENDPOINT")``
in src/services/scraper_scheduler.py, so this code is effectively dormant.

Index: fortress-compliance-docs
Auth:  AWS SigV4 (aoss service)
Dims:  1024 (Bedrock Titan Embed v2)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

_INDEX = "fortress-compliance-docs"
_BATCH_SIZE = 50  # OpenSearch Serverless recommends smaller batches


def _get_client():
    """Return an authenticated OpenSearch client for Serverless."""
    import boto3
    from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

    endpoint = os.environ.get("OPENSEARCH_ENDPOINT", "")
    if not endpoint:
        import boto3 as _boto3
        ssm = _boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        try:
            endpoint = ssm.get_parameter(
                Name="/fortress/pipeline1/opensearch-endpoint"
            )["Parameter"]["Value"]
        except Exception:
            raise RuntimeError("OPENSEARCH_ENDPOINT not set and SSM lookup failed")

    region = os.environ.get("AWS_REGION", "us-east-1")
    credentials = boto3.Session().get_credentials()
    auth = AWSV4SignerAuth(credentials, region, "aoss")

    host = endpoint.replace("https://", "").rstrip("/")
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )


def index_compliance_rule(rule: Dict[str, Any]) -> bool:
    """Index a single compliance rule. Returns True on success."""
    try:
        client = _get_client()
        doc = {
            "rule_id":            rule.get("rule_id", ""),
            "rule_text":          rule.get("rule_text", ""),
            "regulator":          rule.get("regulator", ""),
            "regulation_name":    rule.get("regulation_name", ""),
            "cfr_citation":       rule.get("cfr_citation", ""),
            "jurisdiction":       rule.get("jurisdiction", "federal"),
            "state_code":         rule.get("state_code"),
            "doc_type":           rule.get("doc_type", "regulation"),
            "is_obligation":      rule.get("is_obligation", False),
            "obligation_type":    rule.get("obligation_type"),
            "effective_date":     str(rule.get("effective_date", "")),
            "enforcement_risk":   rule.get("enforcement_risk_score", 0.0),
            "content_hash":       rule.get("content_hash", ""),
            "sync_version":       rule.get("sync_version", 0),
            "is_active":          rule.get("is_active", True),
            "embedding":          rule.get("embedding"),  # 1024-dim list
        }
        # Strip None embedding — can't index null vector
        if not doc["embedding"]:
            doc.pop("embedding")

        client.index(index=_INDEX, id=rule.get("rule_id"), body=doc)
        return True
    except Exception as exc:
        logger.warning("opensearch_index_failed",
                       rule_id=rule.get("rule_id"), error=str(exc))
        return False


def bulk_index_rules(rules: List[Dict[str, Any]]) -> int:
    """Bulk index a list of rules. Returns count of successfully indexed docs."""
    if not rules:
        return 0

    try:
        client = _get_client()
    except Exception as exc:
        logger.warning("opensearch_client_unavailable", error=str(exc))
        return 0

    indexed = 0
    # Process in batches
    for i in range(0, len(rules), _BATCH_SIZE):
        batch = rules[i: i + _BATCH_SIZE]
        actions = []
        for rule in batch:
            doc = {
                "rule_id":         rule.get("rule_id", ""),
                "rule_text":       rule.get("rule_text", "")[:10000],
                "regulator":       rule.get("regulator", ""),
                "regulation_name": rule.get("regulation_name", ""),
                "cfr_citation":    rule.get("cfr_citation", ""),
                "jurisdiction":    rule.get("jurisdiction", "federal"),
                "state_code":      rule.get("state_code"),
                "doc_type":        rule.get("doc_type", "regulation"),
                "is_obligation":   rule.get("is_obligation", False),
                "enforcement_risk":rule.get("enforcement_risk_score", 0.0),
                "content_hash":    rule.get("content_hash", ""),
                "sync_version":    rule.get("sync_version", 0),
                "is_active":       rule.get("is_active", True),
            }
            emb = rule.get("embedding")
            if emb:
                doc["embedding"] = emb

            actions.append({"index": {"_index": _INDEX, "_id": rule.get("rule_id")}})
            actions.append(doc)

        try:
            resp = client.bulk(body=actions)
            batch_errors = [
                item for item in resp.get("items", [])
                if item.get("index", {}).get("error")
            ]
            batch_ok = len(batch) - len(batch_errors)
            indexed += batch_ok
            if batch_errors:
                logger.warning("opensearch_bulk_partial_errors",
                               errors=len(batch_errors), ok=batch_ok)
        except Exception as exc:
            logger.warning("opensearch_bulk_failed",
                           batch_start=i, error=str(exc))

    logger.info("opensearch_bulk_complete",
                total=len(rules), indexed=indexed)
    return indexed


def delete_rule(rule_id: str) -> bool:
    """Remove a rule from the index (called when tombstoned in Aurora)."""
    try:
        client = _get_client()
        client.delete(index=_INDEX, id=rule_id, ignore=[404])
        return True
    except Exception as exc:
        logger.warning("opensearch_delete_failed", rule_id=rule_id, error=str(exc))
        return False
