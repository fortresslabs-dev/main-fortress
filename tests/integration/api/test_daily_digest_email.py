"""Daily P1 sync digest — static verifier for the CFN wiring + handler module.

The digest pipeline has three moving parts; this test locks each of them so a
future edit to the CFN template or the handler can't silently break the
06:00 UTC email:

  1. SNS topic `fortress-p1-digest-${Environment}` with an email subscription
     to the DigestEmail parameter (default: jamie@fortresslabs.dev) — a
     separate channel from AlarmTopic so on-call inbox rules stay clean.
  2. EventBridge Scheduler `fortress-p1-daily-digest-${Environment}` firing
     at 06:00 UTC and targeting the digest task def via the existing
     EventBridge role. The role's RunEcsTask resource list must include the
     digest task-def family or the schedule will fail at fire time.
  3. ECS task def `fortress-pipeline1-digest-${Environment}` running
     `python3 -m src.jobs.daily_digest`, with DIGEST_SNS_TOPIC_ARN wired to
     the new topic so the handler can publish without env-from-SSM lookups.
  4. EcsTaskRoleP1 has `sns:Publish` scoped to the digest topic only — not
     `Resource: "*"` — so the same role can't blast random topics.
  5. The handler file exists and references the scrape_runs table + SNS
     publish, so a stub merge can't pass CI.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPUTE_STACK = REPO_ROOT / "infra" / "cloudformation" / "pipeline1-compute.yml"
HANDLER = REPO_ROOT / "src" / "jobs" / "daily_digest.py"


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
    raise yaml.constructor.ConstructorError(
        None, None, f"unhandled node for !{tag_suffix}", node.start_mark
    )


_CfnLoader.add_multi_constructor("!", _cfn_constructor)


@pytest.fixture(scope="module")
def template() -> dict:
    assert COMPUTE_STACK.exists(), f"{COMPUTE_STACK} not found"
    return yaml.load(COMPUTE_STACK.read_text(), Loader=_CfnLoader)


@pytest.fixture(scope="module")
def resources(template) -> dict:
    return template["Resources"]


# ── SNS topic ──────────────────────────────────────────────────────

def test_digest_topic_exists_with_environment_suffix(resources):
    topic = resources.get("DigestTopic")
    assert topic is not None, "DigestTopic resource missing from compute stack"
    assert topic["Type"] == "AWS::SNS::Topic"
    name = topic["Properties"]["TopicName"]
    assert name == {"Fn::Sub": "fortress-p1-digest-${Environment}"}, (
        f"DigestTopic name must be fortress-p1-digest-${{Environment}} so it is "
        f"distinct from fortress-p1-alarms-${{Environment}} (got {name!r}). "
        f"Sharing the alarm topic would mix the daily OK digest into the on-call channel."
    )


def test_digest_topic_subscribes_digest_email_param(resources, template):
    topic = resources["DigestTopic"]
    subs = topic["Properties"].get("Subscription") or []
    assert subs, "DigestTopic has no Subscription block"
    email_subs = [s for s in subs if s.get("Protocol") == "email"]
    assert email_subs, "DigestTopic has no email subscription"
    assert email_subs[0]["Endpoint"] == {"Ref": "DigestEmail"}, (
        f"DigestTopic email subscription must reference the DigestEmail "
        f"parameter (got {email_subs[0]['Endpoint']!r})."
    )

    digest_param = template.get("Parameters", {}).get("DigestEmail")
    assert digest_param is not None, "DigestEmail parameter missing"
    assert digest_param.get("Default") == "jamie@fortresslabs.dev", (
        f"DigestEmail default must be jamie@fortresslabs.dev so the digest "
        f"reaches the right inbox without a per-deploy override "
        f"(got {digest_param.get('Default')!r})."
    )


def test_digest_topic_not_reusing_alarm_topic(resources):
    """Sanity check — DigestTopic and AlarmTopic must be distinct resources
    with distinct names so the alarm channel doesn't carry the daily OK stream."""
    alarm_name = resources["AlarmTopic"]["Properties"]["TopicName"]
    digest_name = resources["DigestTopic"]["Properties"]["TopicName"]
    assert alarm_name != digest_name


# ── EventBridge schedule ───────────────────────────────────────────

def test_digest_schedule_fires_at_06_utc(resources):
    sched = resources.get("DigestSchedule")
    assert sched is not None, "DigestSchedule resource missing"
    assert sched["Type"] == "AWS::Scheduler::Schedule"

    props = sched["Properties"]
    assert props["ScheduleExpression"] == "cron(0 6 * * ? *)", (
        f"Digest schedule must fire at 06:00 UTC daily — gives the 02:00 UTC "
        f"scraper a 4h buffer before the email is generated "
        f"(got {props['ScheduleExpression']!r})."
    )
    assert props["State"] == "ENABLED", "Digest schedule must ship ENABLED"

    target = props["Target"]
    assert target["EcsParameters"]["TaskDefinitionArn"] == {"Ref": "EcsTaskDefDigestP1"}, (
        "Digest schedule must target the digest task def, not the scraper or API."
    )
    assert target["RoleArn"] == {"Fn::GetAtt": "EventBridgeRole.Arn"}, (
        "Digest schedule must use the shared EventBridge role so its IAM stays "
        "co-located with the scraper schedule's."
    )


def test_eventbridge_role_can_run_digest_taskdef(resources):
    """The shared EventBridgeRole RunEcsTask resource list must include the
    digest task-def family — otherwise the schedule fails at fire time with
    AccessDenied on iam:PassRole-adjacent denials."""
    role = resources["EventBridgeRole"]
    policy = role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    run_task = next(s for s in policy if s["Action"] == ["ecs:RunTask"])
    refs = run_task["Resource"]
    digest_arn = {
        "Fn::Sub": "arn:aws:ecs:${AWS::Region}:${AWS::AccountId}:task-definition/fortress-pipeline1-digest-${Environment}:*"
    }
    assert digest_arn in refs, (
        f"EventBridgeRole must allow ecs:RunTask on the digest task-def family "
        f"(got {refs!r})."
    )


# ── ECS task definition ───────────────────────────────────────────

def test_digest_task_def_runs_handler_module(resources):
    td = resources.get("EcsTaskDefDigestP1")
    assert td is not None, "EcsTaskDefDigestP1 resource missing"
    assert td["Type"] == "AWS::ECS::TaskDefinition"

    props = td["Properties"]
    assert props["Family"] == {"Fn::Sub": "fortress-pipeline1-digest-${Environment}"}
    assert props["RequiresCompatibilities"] == ["FARGATE"]

    container = props["ContainerDefinitions"][0]
    assert container["Command"] == ["python3", "-m", "src.jobs.daily_digest"], (
        f"Digest task must run `python3 -m src.jobs.daily_digest` so the "
        f"handler module is the source of truth (got {container['Command']!r})."
    )

    env = {e["Name"]: e["Value"] for e in container["Environment"]}
    assert env.get("DIGEST_SNS_TOPIC_ARN") == {"Ref": "DigestTopic"}, (
        f"Digest task must receive DIGEST_SNS_TOPIC_ARN as a direct Ref to "
        f"DigestTopic — env-from-SSM would add a per-deploy step "
        f"(got {env.get('DIGEST_SNS_TOPIC_ARN')!r})."
    )
    assert env.get("PIPELINE_MODE") == "pipeline1", (
        "Digest task must set PIPELINE_MODE=pipeline1 so config.py loads the "
        "P1 Secrets Manager bundle (DATABASE_URL etc.)."
    )


def test_digest_task_def_uses_shared_p1_image_and_roles(resources):
    """Reusing the pipeline1 image + task role is the whole reason we picked
    ECS over Lambda — locks in that we didn't drift to a one-off image/role.

    The image tag, though, is decoupled (see next test) — the digest task
    must use ${DigestImageTag}, NOT ${ImageTag}.
    """
    td = resources["EcsTaskDefDigestP1"]["Properties"]
    assert td["TaskRoleArn"] == {"Fn::GetAtt": "EcsTaskRoleP1.Arn"}, (
        "Digest task must reuse EcsTaskRoleP1 (which carries the SNS Publish + DB grants)."
    )
    assert td["ExecutionRoleArn"] == {"Fn::GetAtt": "EcsExecutionRoleP1.Arn"}
    image = td["ContainerDefinitions"][0]["Image"]
    assert image == {
        "Fn::Sub": "${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/fortress/pipeline1:${DigestImageTag}"
    }, f"Digest task must reuse the pipeline1 ECR repo with DigestImageTag, got {image!r}"


def test_digest_image_tag_is_decoupled_from_api_image_tag(resources, template):
    """Regression lock: a future edit must not collapse DigestImageTag back into
    ImageTag. Coupling them would mean every API roll also rolls the digest
    task (and vice versa), defeating the reason this parameter exists."""
    params = template.get("Parameters", {})
    assert "DigestImageTag" in params, (
        "DigestImageTag parameter missing — required to keep the digest task's "
        "image independent of the API task's image."
    )
    assert "Default" not in params["DigestImageTag"], (
        "DigestImageTag must NOT have a default — every update-stack must pass "
        "it explicitly, otherwise it can silently drift to whatever ImageTag is."
    )

    api_image = resources["EcsTaskDefP1"]["Properties"]["ContainerDefinitions"][0]["Image"]
    scraper_image = resources["EcsTaskDefScraperP1"]["Properties"]["ContainerDefinitions"][0]["Image"]
    digest_image = resources["EcsTaskDefDigestP1"]["Properties"]["ContainerDefinitions"][0]["Image"]

    assert "${ImageTag}" in api_image["Fn::Sub"], "API task must keep using ${ImageTag}"
    assert "${ImageTag}" in scraper_image["Fn::Sub"], "Scraper task must keep using ${ImageTag}"
    assert "${DigestImageTag}" in digest_image["Fn::Sub"], (
        "Digest task must use ${DigestImageTag}, not ${ImageTag}."
    )
    assert "${ImageTag}" not in digest_image["Fn::Sub"], (
        "Digest task must NOT reference ${ImageTag} — a bump to ImageTag would "
        "then also roll the digest task, defeating decoupling."
    )


# ── IAM grant ──────────────────────────────────────────────────────

def test_task_role_sns_publish_scoped_to_digest_topic(resources):
    """sns:Publish must be scoped to the digest topic only — wildcarding it
    would let the same task role publish to fortress-p1-alarms or any other
    topic in the account."""
    role = resources["EcsTaskRoleP1"]
    statements = role["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]

    sns_stmts = [
        s for s in statements
        if "sns:Publish" in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])
    ]
    assert sns_stmts, "EcsTaskRoleP1 has no sns:Publish grant — digest will fail to publish"

    for stmt in sns_stmts:
        resources_list = stmt["Resource"] if isinstance(stmt["Resource"], list) else [stmt["Resource"]]
        assert "*" not in resources_list, (
            "sns:Publish must NOT be Resource: '*' — scope it to DigestTopic only."
        )
        assert {"Ref": "DigestTopic"} in resources_list, (
            f"sns:Publish must reference DigestTopic via !Ref (got {resources_list!r})."
        )


# ── Handler module ─────────────────────────────────────────────────

def test_digest_handler_module_exists_and_queries_scrape_runs():
    assert HANDLER.exists(), f"{HANDLER} missing — digest task will crash at startup"
    source = HANDLER.read_text()
    assert "scrape_runs" in source, (
        "daily_digest.py must query the scrape_runs table — that's the only "
        "source with per-connector error_message."
    )
    assert "sns" in source.lower(), "daily_digest.py must publish to SNS"
    assert "DIGEST_SNS_TOPIC_ARN" in source, (
        "daily_digest.py must read DIGEST_SNS_TOPIC_ARN — matches the env var "
        "injected by the digest task def."
    )


def test_digest_handler_formats_failure_reason_inline():
    """Spec: failed rows are rendered as `Failed: <reason>` in the body. Lock
    the format so a refactor can't drop the reason field on the floor."""
    source = HANDLER.read_text()
    assert "Failed:" in source, (
        "Failed rows must render as `Failed: <error_message>` per the digest spec."
    )
