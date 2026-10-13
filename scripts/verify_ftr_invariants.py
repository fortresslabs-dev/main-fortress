#!/usr/bin/env python3
"""
scripts/verify_ftr_invariants.py

Asserts FTR-readiness invariants in committed infra templates so a future PR
can't silently regress them. Run from CI on every PR + push to main.

Currently checks:
  • pipeline2-data.yml — DbMultiAz default is "true" (FTR §13.1 B3)
  • pipeline2-data.yml — Rules.EnforceMultiAzInProduction asserts DbMultiAz="true"
    when Environment=production
  • pipeline2-dashboard.yml — CognitoUserPool.MfaConfiguration is OPTIONAL or ON,
    never OFF (FTR §13.1 B2)
  • pipeline2-dashboard.yml — CognitoUserPool.EnabledMfas == [SOFTWARE_TOKEN_MFA]
    (SMS rejected — SIM-swap risk)
  • pipeline1-security.yml — Primary + Backup SMS paging subscriptions on
    SecurityAlertsTopic exist (FTR §13.1 B9 — AWS-only paging path)

Exit code: 0 on pass, 1 on any failure. Failures print actionable messages.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
P2_DATA_TEMPLATE = REPO_ROOT / "infra" / "cloudformation" / "pipeline2-data.yml"
P2_DASHBOARD_TEMPLATE = (
    REPO_ROOT / "infra" / "cloudformation" / "pipeline2-dashboard.yml"
)
P1_SECURITY_TEMPLATE = (
    REPO_ROOT / "infra" / "cloudformation" / "pipeline1-security.yml"
)


class _CfnLoader(yaml.SafeLoader):
    """Loader that tolerates CFN's !Ref / !Sub / !Equals etc. tags."""


def _construct(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node, deep=True)}
    return {tag_suffix: loader.construct_mapping(node, deep=True)}


_CfnLoader.add_multi_constructor("!", _construct)


def _load(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return yaml.load(fh, Loader=_CfnLoader)


def _fail(msg: str) -> None:
    print(f"  ✗ FAIL: {msg}")


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def check_p2_data_multiaz() -> list[str]:
    """Returns list of failure messages (empty = pass)."""
    failures: list[str] = []
    doc = _load(P2_DATA_TEMPLATE)

    # 1. Default must be the string "true"
    params = doc.get("Parameters", {})
    db_multi_az = params.get("DbMultiAz", {})
    default = db_multi_az.get("Default")
    if default != "true":
        failures.append(
            f"{P2_DATA_TEMPLATE.name}: DbMultiAz default is "
            f"{default!r}, expected 'true' (FTR §13.1 B3)."
        )
    else:
        _ok(f"{P2_DATA_TEMPLATE.name} DbMultiAz default = 'true'")

    # 2. Rules.EnforceMultiAzInProduction must exist and gate prod on "true"
    rules = doc.get("Rules", {})
    rule = rules.get("EnforceMultiAzInProduction")
    if not rule:
        failures.append(
            f"{P2_DATA_TEMPLATE.name}: Rules.EnforceMultiAzInProduction missing — "
            "this is the deploy-time gate that blocks prod with DbMultiAz=false."
        )
        return failures

    # The Rule must condition on Environment=production AND assert DbMultiAz=true.
    rule_cond = rule.get("RuleCondition", {})
    if "Equals" not in rule_cond:
        failures.append(
            f"{P2_DATA_TEMPLATE.name}: EnforceMultiAzInProduction.RuleCondition "
            "is not an !Equals expression — cannot verify it gates on Environment."
        )

    asserts = rule.get("Assertions", [])
    if not asserts:
        failures.append(
            f"{P2_DATA_TEMPLATE.name}: EnforceMultiAzInProduction has no Assertions."
        )

    asserts_db_true = any(
        "Equals" in a.get("Assert", {})
        and "true" in str(a.get("Assert", {}).get("Equals", ""))
        for a in asserts
    )
    if not asserts_db_true:
        failures.append(
            f"{P2_DATA_TEMPLATE.name}: EnforceMultiAzInProduction does not assert "
            "DbMultiAz='true'."
        )

    if not failures:
        _ok(
            f"{P2_DATA_TEMPLATE.name} Rules.EnforceMultiAzInProduction gates prod on DbMultiAz='true'"
        )
    return failures


def check_p2_dashboard_mfa() -> list[str]:
    """Returns list of failure messages (empty = pass).

    Asserts the bank dashboard Cognito user pool template has:
      • MfaConfiguration ∈ {OPTIONAL, ON} — never OFF
      • EnabledMfas exactly [SOFTWARE_TOKEN_MFA] — SMS rejected (SIM-swap risk)
    """
    failures: list[str] = []
    doc = _load(P2_DASHBOARD_TEMPLATE)
    resources = doc.get("Resources", {})

    pool = resources.get("CognitoUserPool")
    if not pool:
        failures.append(
            f"{P2_DASHBOARD_TEMPLATE.name}: CognitoUserPool resource missing."
        )
        return failures

    props = pool.get("Properties", {})
    mfa_config = props.get("MfaConfiguration")
    enabled_mfas = props.get("EnabledMfas")

    if mfa_config not in ("OPTIONAL", "ON"):
        failures.append(
            f"{P2_DASHBOARD_TEMPLATE.name}: CognitoUserPool.MfaConfiguration is "
            f"{mfa_config!r}, expected 'OPTIONAL' or 'ON' (FTR §13.1 B2). "
            "Note: 'OPTIONAL' is the staged-rollout starting state; tighten to 'ON' "
            "after enrolment grace window."
        )
    else:
        _ok(
            f"{P2_DASHBOARD_TEMPLATE.name} CognitoUserPool.MfaConfiguration = "
            f"{mfa_config!r} (not OFF)"
        )

    if enabled_mfas != ["SOFTWARE_TOKEN_MFA"]:
        failures.append(
            f"{P2_DASHBOARD_TEMPLATE.name}: CognitoUserPool.EnabledMfas is "
            f"{enabled_mfas!r}, expected ['SOFTWARE_TOKEN_MFA'] exactly. "
            "SMS_MFA is rejected — SIM-swap risk."
        )
    else:
        _ok(
            f"{P2_DASHBOARD_TEMPLATE.name} CognitoUserPool.EnabledMfas = "
            "['SOFTWARE_TOKEN_MFA'] (TOTP-only, no SMS)"
        )

    return failures


def check_p1_security_sms_paging() -> list[str]:
    """Returns list of failure messages (empty = pass).

    Asserts the security stack template carries both SMS paging
    subscription resources gated on their respective parameters. The
    invariant is on the *resource* existence (template can route SMS),
    not on the parameter values (which are operational).
    """
    failures: list[str] = []
    doc = _load(P1_SECURITY_TEMPLATE)
    resources = doc.get("Resources", {})
    params = doc.get("Parameters", {})
    conditions = doc.get("Conditions", {})

    # Parameters
    for param in ("PrimaryPagingPhoneE164", "BackupPagingPhoneE164"):
        if param not in params:
            failures.append(
                f"{P1_SECURITY_TEMPLATE.name}: parameter {param} missing — "
                "required for FTR §13.1 B9 AWS-only paging path."
            )

    # Conditions
    for cond in ("HasPrimaryPagingPhone", "HasBackupPagingPhone"):
        if cond not in conditions:
            failures.append(
                f"{P1_SECURITY_TEMPLATE.name}: condition {cond} missing — "
                "required to gate paging subscriptions on empty phone numbers."
            )

    # Subscription resources
    sub_resources = {
        "SecurityAlertsTopicPrimaryPagingSubscription": "HasPrimaryPagingPhone",
        "SecurityAlertsTopicBackupPagingSubscription": "HasBackupPagingPhone",
    }
    for res_name, expected_condition in sub_resources.items():
        res = resources.get(res_name)
        if not res:
            failures.append(
                f"{P1_SECURITY_TEMPLATE.name}: {res_name} resource missing — "
                "this is the SNS-SMS paging subscription required for B9."
            )
            continue
        if res.get("Type") != "AWS::SNS::Subscription":
            failures.append(
                f"{P1_SECURITY_TEMPLATE.name}: {res_name} is not "
                "AWS::SNS::Subscription."
            )
        if res.get("Condition") != expected_condition:
            failures.append(
                f"{P1_SECURITY_TEMPLATE.name}: {res_name} must be gated on "
                f"Condition: {expected_condition}, got {res.get('Condition')!r}."
            )
        props = res.get("Properties", {})
        if props.get("Protocol") != "sms":
            failures.append(
                f"{P1_SECURITY_TEMPLATE.name}: {res_name}.Properties.Protocol "
                f"is {props.get('Protocol')!r}, expected 'sms'."
            )

    if not failures:
        _ok(
            f"{P1_SECURITY_TEMPLATE.name} SecurityAlertsTopic carries primary + "
            "backup SMS paging subscriptions (gated on PrimaryPagingPhoneE164 / "
            "BackupPagingPhoneE164 params)"
        )
    return failures


def main() -> int:
    print("FTR invariants check")
    print("====================")
    failures: list[str] = []
    failures += check_p2_data_multiaz()
    failures += check_p2_dashboard_mfa()
    failures += check_p1_security_sms_paging()

    print()
    if failures:
        print(f"FAILED — {len(failures)} invariant(s) regressed:")
        for f in failures:
            print(f"  • {f}")
        return 1
    print("PASSED — all FTR invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
