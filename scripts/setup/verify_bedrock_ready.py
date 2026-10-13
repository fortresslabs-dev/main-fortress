"""Bank-onboarding pre-flight: verify Bedrock is ready in this AWS account.

Per ``feedback_bedrock_anthropic_use_case_form`` and
``feedback_bedrock_marketplace_iam``, Claude 4.x invocation needs two
one-time configurations per AWS account that often fail silently if not
done first:

  1. The Anthropic use-case form (per Claude family — Sonnet, Haiku)
     submitted in the Bedrock console; without it, InvokeModel returns
     ``ResourceNotFoundException`` even with correct IAM.
  2. The task IAM role having ``aws-marketplace:Subscribe`` /
     ``ViewSubscriptions`` / ``Unsubscribe`` on Resource "*", or the
     first InvokeModel fails with ``AccessDeniedException`` referencing
     "required AWS Marketplace actions".

This script tries a tiny no-op invocation of each model we depend on
and reports which gate (if any) is failing, plus the action the bank
must take. Designed to be run by bank-onboarding ops as the FIRST
configuration check after deploying a P2 stack.

Run:
  python -m scripts.setup.verify_bedrock_ready
"""
from __future__ import annotations

import json
import logging
import os
import sys

import boto3
from botocore.exceptions import ClientError


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# Models we depend on per the P2 stack. IDs MUST use the us. inference-
# profile prefix per feedback_bedrock_inference_profile_prefix.
_MODELS = [
    ("Sonnet 4.x (agent generation, board-report summariser)",
     os.getenv("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-v1:0")),
    ("Haiku 4.5 (lightweight classification)",
     os.getenv("BEDROCK_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-v1:0")),
    ("Titan v2 embed (SDN + compliance_rules vectors)",
     "amazon.titan-embed-text-v2:0"),
]


def _ping(client, model_id: str) -> tuple[str, str]:
    """Return (status, detail). status: ok / use_case_form / marketplace / iam / other."""
    if model_id.startswith("amazon.titan-embed"):
        body = json.dumps({"inputText": "ping", "dimensions": 1024, "normalize": True})
    else:
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        })

    try:
        client.invoke_model(modelId=model_id, body=body,
                            contentType="application/json", accept="application/json")
        return "ok", "InvokeModel succeeded"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        message = exc.response.get("Error", {}).get("Message", "")
        if code == "ResourceNotFoundException":
            return "use_case_form", (
                "ResourceNotFoundException — submit the Anthropic use-case "
                "form in the Bedrock console for this Claude family. See "
                "feedback_bedrock_anthropic_use_case_form."
            )
        if code == "AccessDeniedException" and "marketplace" in message.lower():
            return "marketplace", (
                "AccessDeniedException citing AWS Marketplace — add "
                "aws-marketplace:Subscribe / ViewSubscriptions / "
                "Unsubscribe on Resource \"*\" to the task role. See "
                "feedback_bedrock_marketplace_iam."
            )
        if code == "AccessDeniedException":
            return "iam", f"AccessDeniedException: {message}"
        return "other", f"{code}: {message}"
    except Exception as exc:  # noqa: BLE001
        return "other", f"{exc.__class__.__name__}: {exc}"


def main() -> int:
    region = os.getenv("AWS_REGION", "us-east-1")
    client = boto3.client("bedrock-runtime", region_name=region)
    log.info("verify_bedrock region=%s", region)

    any_failed = False
    for label, model_id in _MODELS:
        status, detail = _ping(client, model_id)
        if status == "ok":
            log.info("ok  | %-65s | %s", label, model_id)
        else:
            any_failed = True
            log.error("fail | %-65s | %s | %s", label, model_id, detail)

    if any_failed:
        log.error("verify_bedrock failures — fix the gates above before continuing onboarding")
        return 1
    log.info("verify_bedrock all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
