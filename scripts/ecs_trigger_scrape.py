#!/usr/bin/env python3
"""
Trigger the Pipeline 1 scrape cycle on the running ECS task via boto3.
Credentials are loaded from AWS Secrets Manager — no local env vars needed.

Usage:
    python -m scripts.ecs_trigger_scrape

First-time setup (store credentials):
    python -m src.utils.secrets_manager store
"""
from __future__ import annotations

import sys
import pathlib
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CLUSTER = "fortress-cluster"
SERVICE = "fortress-pipeline1-service"
CONTAINER = "pipeline1"
REGION = "us-east-1"
COMMAND = "python -m scripts.trigger_scrape"


def get_running_task_arn(ecs) -> str:
    resp = ecs.list_tasks(cluster=CLUSTER, serviceName=SERVICE, desiredStatus="RUNNING")
    arns = resp.get("taskArns", [])
    if not arns:
        raise RuntimeError(f"No running tasks found in service {SERVICE}")
    return arns[0]


def trigger(task_arn: str, ecs) -> None:
    print(f"[ecs_trigger] Cluster  : {CLUSTER}")
    print(f"[ecs_trigger] Task     : {task_arn.split('/')[-1]}")
    print(f"[ecs_trigger] Command  : {COMMAND}")
    print()

    resp = ecs.execute_command(
        cluster=CLUSTER,
        task=task_arn,
        container=CONTAINER,
        interactive=True,
        command=COMMAND,
    )
    session = resp["session"]
    print(f"[ecs_trigger] Session ID : {session['sessionId']}")
    print(f"[ecs_trigger] Stream URL : {session['streamUrl']}")
    print()
    print("[ecs_trigger] Command dispatched. Follow logs with:")
    print(f"  aws logs tail /fortress/pipeline1 --follow --region {REGION}")


def main() -> None:
    import boto3
    try:
        from src.utils.secrets_manager import get_ecs_client
        ecs = get_ecs_client(REGION)
    except Exception:
        # Fall back to default credential chain (env vars, ~/.aws/credentials, IAM role)
        ecs = boto3.client("ecs", region_name=REGION)

    task_arn = get_running_task_arn(ecs)
    trigger(task_arn, ecs)


if __name__ == "__main__":
    main()
