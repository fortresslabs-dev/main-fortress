"""
H4 — WAF posture for the P1 sync API.

Before H4, the WebACL only had a rate-limit rule (100 req / 5 min/IP on
`/api/sync*`) and a managed common rule set. A paginated initial-seed
burst from a legitimate bank — `/sync/initial` (N pages) + `/sync/ofac-sdn`
(M pages) + `/sync/sdn-vectors` (~26 pages at 2k page_size) + … — easily
exceeds 100 requests in 5 minutes and was getting throttled. The intended
posture (per the file comment and `register-bank-ip.sh`) was an IP
allowlist for registered bank NAT EIPs that bypasses the rate limit,
but no `AWS::WAFv2::IPSet` resource existed in the template and the
WebACL had no allow-rule referencing one — so `register-bank-ip.sh` was
populating an IPSet nothing read from.

This test parses `infra/cloudformation/pipeline1-compute.yml` and locks
the post-fix posture:

  - `BankIpAllowSet` (`AWS::WAFv2::IPSet`, REGIONAL, IPV4) exists.
  - The WebACL's first-priority rule (Priority 0) is `AllowBankIps`,
    statement `IPSetReferenceStatement -> BankIpAllowSet`, action Allow.
  - The rate-limit rule fires at a LATER priority (so allow takes
    precedence), targets `/api/sync*` only (scope-down), aggregates by
    IP, EvaluationWindowSec is 300.
  - The managed-rules-common rule is still present.
  - The associated load balancer is the production ALB (`AlbP1`).

Operational note (NOT enforced here): a `cloudformation update-stack` on
pipeline1-compute will reset `BankIpAllowSet.Addresses` to `[]`. After
each compute-stack update, `infra/scripts/register-bank-ip.sh` must be
re-run for every registered bank. EIPs survive in SSM under
`/fortress/banks/<bank>/nat-eip` for that re-hydration.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPUTE_STACK = REPO_ROOT / "infra" / "cloudformation" / "pipeline1-compute.yml"


# ── CFN short-tag loader (same pattern as test_aurora_kms_encryption_b12) ──

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
def resources() -> dict:
    assert COMPUTE_STACK.exists(), f"{COMPUTE_STACK} not found"
    return yaml.load(COMPUTE_STACK.read_text(), Loader=_CfnLoader)["Resources"]


@pytest.fixture(scope="module")
def webacl_rules(resources) -> list[dict]:
    return resources["WafWebAclP1"]["Properties"]["Rules"]


def _find_rule(rules: list[dict], name: str) -> dict | None:
    return next((r for r in rules if r.get("Name") == name), None)


# ── BankIpAllowSet resource ─────────────────────────────────────────

def test_h4_bank_ip_allow_set_exists(resources):
    """The IPSet referenced by register-bank-ip.sh must be CFN-managed —
    otherwise the WebACL's Allow rule points at an ARN that doesn't
    resolve and the entire sync API becomes rate-limited."""
    assert "BankIpAllowSet" in resources, (
        "BankIpAllowSet (AWS::WAFv2::IPSet) is missing from "
        "pipeline1-compute.yml. The WebACL's AllowBankIps rule cannot "
        "reference an IPSet that doesn't exist."
    )
    ipset = resources["BankIpAllowSet"]
    assert ipset["Type"] == "AWS::WAFv2::IPSet"
    props = ipset["Properties"]
    assert props["Scope"] == "REGIONAL", (
        f"BankIpAllowSet.Scope must be REGIONAL to be referenced by an "
        f"ALB-attached WebACL (got {props.get('Scope')!r})."
    )
    assert props["IPAddressVersion"] == "IPV4", (
        f"BankIpAllowSet.IPAddressVersion must be IPV4 — "
        f"register-bank-ip.sh ships /32 CIDRs (got {props.get('IPAddressVersion')!r})."
    )


# ── AllowBankIps rule ───────────────────────────────────────────────

def test_h4_allow_rule_exists_at_priority_zero(webacl_rules):
    """The Allow rule must run BEFORE the rate-limit rule, otherwise the
    rate-limit blocks legitimate paginated bank traffic regardless of
    allowlist membership."""
    allow_rule = _find_rule(webacl_rules, "AllowBankIps")
    assert allow_rule is not None, (
        "WafWebAclP1 has no AllowBankIps rule — the IP allowlist is "
        "not actually enforced. register-bank-ip.sh populates an IPSet "
        "that nothing reads from."
    )
    assert allow_rule["Priority"] == 0, (
        f"AllowBankIps.Priority must be 0 (lowest = highest-precedence). "
        f"Got {allow_rule['Priority']!r}. If RateLimitSyncApi runs first, "
        f"legit traffic gets blocked before the allow has a chance to fire."
    )
    assert "Allow" in allow_rule["Action"], (
        f"AllowBankIps.Action must be {{Allow: {{}}}} (got {allow_rule['Action']!r})."
    )


def test_h4_allow_rule_references_bank_ip_set(webacl_rules):
    allow_rule = _find_rule(webacl_rules, "AllowBankIps")
    assert allow_rule is not None
    stmt = allow_rule["Statement"]
    assert "IPSetReferenceStatement" in stmt, (
        f"AllowBankIps must use an IPSetReferenceStatement (got {list(stmt.keys())!r})."
    )
    arn = stmt["IPSetReferenceStatement"].get("Arn")
    # CFN short-tag loader represents `!GetAtt BankIpAllowSet.Arn` as
    # {"Fn::GetAtt": "BankIpAllowSet.Arn"}.
    assert isinstance(arn, dict) and "Fn::GetAtt" in arn, (
        f"AllowBankIps must reference BankIpAllowSet via GetAtt "
        f"(got {arn!r}). A literal ARN would not survive stack rebuild."
    )
    target = arn["Fn::GetAtt"]
    target_name = target[0] if isinstance(target, list) else target.split(".")[0]
    assert target_name == "BankIpAllowSet", (
        f"AllowBankIps references {target_name!r} instead of BankIpAllowSet."
    )


# ── RateLimitSyncApi rule ───────────────────────────────────────────

def test_h4_rate_limit_runs_after_allow(webacl_rules):
    """Even after H4 raises the allow rule to priority 0, the rate limit
    must remain in place for unknown IPs — `/api/sync` requires HMAC,
    so anyone not allowlisted has no business at 100+ req / 5 min."""
    rate_rule = _find_rule(webacl_rules, "RateLimitSyncApi")
    assert rate_rule is not None, "RateLimitSyncApi rule was removed"
    assert rate_rule["Priority"] >= 1, (
        f"RateLimitSyncApi.Priority must be ≥ 1 so AllowBankIps fires "
        f"first (got {rate_rule['Priority']!r})."
    )


def test_h4_rate_limit_scope_down_to_sync_only(webacl_rules):
    """The rate limit must NOT apply to /health or /openapi.json or
    any non-sync route — otherwise dashboards and probes get throttled."""
    rate_rule = _find_rule(webacl_rules, "RateLimitSyncApi")
    rate = rate_rule["Statement"]["RateBasedStatement"]
    scope_down = rate.get("ScopeDownStatement")
    assert scope_down is not None, "RateLimitSyncApi must have a ScopeDownStatement"

    byte_match = scope_down.get("ByteMatchStatement")
    assert byte_match is not None, (
        f"ScopeDownStatement must use ByteMatchStatement on URI path "
        f"(got {list(scope_down.keys())!r})."
    )
    assert byte_match["SearchString"] == "/api/sync", (
        f"ScopeDownStatement.SearchString must be '/api/sync' "
        f"(got {byte_match['SearchString']!r})."
    )
    assert byte_match["PositionalConstraint"] == "STARTS_WITH", (
        f"ScopeDownStatement.PositionalConstraint must be STARTS_WITH so "
        f"every /api/sync/* sub-path is covered "
        f"(got {byte_match['PositionalConstraint']!r})."
    )


def test_h4_rate_limit_window_and_key(webacl_rules):
    rate_rule = _find_rule(webacl_rules, "RateLimitSyncApi")
    rate = rate_rule["Statement"]["RateBasedStatement"]
    assert rate["EvaluationWindowSec"] == 300, (
        f"RateLimitSyncApi.EvaluationWindowSec must be 300 (5 min) so "
        f"CloudWatch alarms can key off the standard window "
        f"(got {rate['EvaluationWindowSec']!r})."
    )
    assert rate["AggregateKeyType"] == "IP", (
        f"RateLimitSyncApi.AggregateKeyType must be IP — a forwarded-IP "
        f"key without XFF normalization can be spoofed "
        f"(got {rate['AggregateKeyType']!r})."
    )
    assert rate["Limit"] >= 100, (
        f"RateLimitSyncApi.Limit must be ≥ 100 to avoid blocking even a "
        f"small unauthenticated probing burst from a legit operator at "
        f"a coffee shop (got {rate['Limit']!r})."
    )
    assert "Block" in rate_rule["Action"], (
        f"RateLimitSyncApi.Action must be {{Block: {{}}}} on breach "
        f"(got {rate_rule['Action']!r})."
    )


# ── Managed rule set still present ──────────────────────────────────

def test_h4_aws_managed_common_rules_still_present(webacl_rules):
    """OWASP Top 10 coverage must not be regressed."""
    rule = _find_rule(webacl_rules, "AWSManagedRulesCommon")
    assert rule is not None, (
        "AWSManagedRulesCommon (CommonRuleSet) was removed — OWASP "
        "Top 10 coverage is now off."
    )
    stmt = rule["Statement"]
    mrg = stmt.get("ManagedRuleGroupStatement", {})
    assert mrg.get("VendorName") == "AWS"
    assert mrg.get("Name") == "AWSManagedRulesCommonRuleSet"


# ── WebACL association ──────────────────────────────────────────────

def test_h4_webacl_attached_to_alb(resources):
    """The WebACL must be associated with the ALB; an unassociated WebACL
    is silently ignored at request time."""
    assoc = resources.get("WafAssociation")
    assert assoc is not None, "WafAssociation resource missing"
    assert assoc["Type"] == "AWS::WAFv2::WebACLAssociation"
    props = assoc["Properties"]

    target = props.get("ResourceArn")
    assert isinstance(target, dict) and "Ref" in target and target["Ref"] == "AlbP1", (
        f"WafAssociation.ResourceArn must be !Ref AlbP1 (got {target!r})."
    )
    webacl_arn = props.get("WebACLArn")
    assert isinstance(webacl_arn, dict) and "Fn::GetAtt" in webacl_arn, (
        f"WafAssociation.WebACLArn must be !GetAtt WafWebAclP1.Arn (got {webacl_arn!r})."
    )
    target_name = webacl_arn["Fn::GetAtt"]
    target_name = target_name[0] if isinstance(target_name, list) else target_name.split(".")[0]
    assert target_name == "WafWebAclP1"
