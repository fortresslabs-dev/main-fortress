"""
AWS Secrets Manager helper for storing and retrieving credentials.

Store once:
    python -m src.utils.secrets_manager store

Use in code:
    from src.utils.secrets_manager import get_aws_session
    session = get_aws_session()
    ecs = session.client("ecs", region_name="us-east-1")
"""
from __future__ import annotations

import json
import boto3
from functools import lru_cache
from typing import Optional

SECRET_NAME = "fortress/pipeline1/aws-credentials"
REGION = "us-east-1"


def store_credentials(
    access_key_id: str,
    secret_access_key: str,
    session_token: Optional[str] = None,
    region: str = REGION,
) -> None:
    """Store AWS credentials in Secrets Manager. Run once from a privileged session."""
    client = boto3.client("secretsmanager", region_name=region)
    payload: dict = {
        "AWS_ACCESS_KEY_ID": access_key_id,
        "AWS_SECRET_ACCESS_KEY": secret_access_key,
    }
    if session_token:
        payload["AWS_SESSION_TOKEN"] = session_token

    try:
        client.put_secret_value(
            SecretId=SECRET_NAME,
            SecretString=json.dumps(payload),
        )
        print(f"[ok] Updated secret: {SECRET_NAME}")
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(
            Name=SECRET_NAME,
            Description="AWS credentials used by fortress-pipeline1 ECS ops scripts",
            SecretString=json.dumps(payload),
        )
        print(f"[ok] Created secret: {SECRET_NAME}")


@lru_cache(maxsize=1)
def _load_credentials(region: str = REGION) -> dict:
    """Fetch credentials from Secrets Manager (cached per process)."""
    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=SECRET_NAME)
    return json.loads(response["SecretString"])


def get_aws_session(region: str = REGION) -> boto3.Session:
    """Return a boto3 Session authenticated via Secrets Manager credentials."""
    creds = _load_credentials(region)
    return boto3.Session(
        aws_access_key_id=creds["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=creds["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=creds.get("AWS_SESSION_TOKEN"),
        region_name=region,
    )


def get_ecs_client(region: str = REGION):
    return get_aws_session(region).client("ecs", region_name=region)


# ── CLI: store credentials interactively ──────────────────────────────────────
if __name__ == "__main__":
    import sys
    import getpass

    if len(sys.argv) < 2 or sys.argv[1] != "store":
        print("Usage: python -m src.utils.secrets_manager store")
        sys.exit(1)

    print(f"Storing credentials to Secrets Manager → {SECRET_NAME}")
    key_id = getpass.getpass("  AWS_ACCESS_KEY_ID     : ")
    secret = getpass.getpass("  AWS_SECRET_ACCESS_KEY : ")
    token = getpass.getpass("  AWS_SESSION_TOKEN (leave blank if permanent): ")

    store_credentials(key_id, secret, token or None)
