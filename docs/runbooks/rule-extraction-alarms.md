# Rule extraction — CloudWatch alarms & log queries

## What this is

The regex-first / LLM-fallback rule extraction pipeline emits CloudWatch
Embedded Metric Format (EMF) records on every terminal outcome. CW Alarms
can subscribe to these metric names directly — no separate metric pipeline.

Decision path per rule (see `src/services/obligation_extractor.extract_for_rule`):

```
                 ┌──────────────┐
                 │ regex pass   │
                 └──────┬───────┘
                        │
              has cite AND obligations?
              ┌──────── │ ────────┐
              ▼ yes              ▼ no
        ┌─────────────┐    LLM_EXTRACTION_MODE?
        │RegexSuffici-│    ┌──────┴──────┐
        │   ent       │    │  off / empty │ ──► RegexInsufficient
        └─────────────┘    │              │
                           │  fallback    │ ──► extract_rule()
                           │              │      ├── ok   ──► LlmFallbackInvoked
                           │              │      ├── err  ──► LlmFallbackFailed
                           │              │      └── low  ──► LlmFallbackLowConfidence
                           │              │                   + LlmFallbackFailed
                           │  shadow      │ ──► run LLM, log diff,
                           │              │     persist regex ──► LlmShadowRun
                           └──────────────┘
```

EMF namespace: `Fortress/P1/Extraction`

## Metrics

| Metric | Emitted by | Dimensions | When | Level |
|---|---|---|---|---|
| `BedrockInvokeFailures` | `bedrock_extractor.py` | `Schema, ErrorClass` | `invoke_model` raises (throttling, network, auth). | **ERROR** + `exc_info` |
| `ValidationFailures` | `bedrock_extractor.py` | `Schema` | LLM tool_use payload fails Pydantic validation. | **ERROR** |
| `ExtractionSuccesses` | `bedrock_extractor.py` | `Schema` | Successful extraction passes schema validation. | INFO |
| `BedrockInputTokens` / `BedrockOutputTokens` | `bedrock_extractor.py` | `Schema` | Per-call token counts (use for cost dashboards). | INFO |
| `RegexSufficient` | `obligation_extractor.py` | `Schema, Regulator` | Regex pass found both a primary CFR AND ≥1 obligation. No Bedrock call. | INFO |
| `RegexInsufficient` | `obligation_extractor.py` | `Schema, Regulator, Gap` | Regex was incomplete but LLM was disabled (`off`) or input empty. Gap ∈ {`CiteMissing`, `ObligationsMissing`, `BothMissing`}. | INFO |
| `LlmFallbackInvoked` | `obligation_extractor.py` | `Schema, Regulator` | LLM fired as fallback and produced output that filled a regex gap. | INFO |
| `LlmFallbackFailed` | `obligation_extractor.py` | `Schema, Regulator, Reason` | LLM was invoked but couldn't fill the gap. Reason ∈ {`LowConfidence`, exception class}. | WARNING |
| `LlmFallbackLowConfidence` | `obligation_extractor.py` | `Schema, Regulator` | LLM responded with confidence < `LLM_EXTRACTION_MIN_CONFIDENCE` (default 0.5). Counted separately so `LlmFallbackFailed` isn't drowned by model uncertainty. | WARNING |
| `LlmShadowRun` | `obligation_extractor.py` | `Schema, Regulator` | Shadow mode — LLM ran but regex output is what was persisted. See `llm_shadow_diff` log event for the diff. | INFO |
| `BackfillRuleErrors` | `backfill_rule_extractions.py` | `Schema, Regulator, Phase` | Unexpected error in backfill (programming error, regex layer crash). | ERROR |
| `BackfillRulesScanned` / `BackfillRulesWritten` / `BackfillRegexSufficient` / `BackfillLlmInvoked` / `BackfillErrors` | `backfill_rule_extractions.py` | `Schema, DryRun` | Per-run summary counters. | INFO |

## Recommended alarms

| Alarm name | Metric | Threshold | Period | Severity |
|---|---|---|---|---|
| `RuleExtraction-BedrockDown` | `BedrockInvokeFailures` | `>= 3` | 5 min | **Page** |
| `RuleExtraction-ModelDrift` | `ValidationFailures` | `>= 5` | 5 min | Ticket |
| `RuleExtraction-FallbackFailRate` | `LlmFallbackFailed / (LlmFallbackInvoked + LlmFallbackFailed)` (math expr) | `>= 0.2` | 15 min | Ticket |
| `RuleExtraction-LowConfidenceSpike` | `LlmFallbackLowConfidence` | `>= 20` | 15 min | Ticket |
| `RuleExtraction-RegexCoverageDrop` | `RegexSufficient / (RegexSufficient + RegexInsufficient + LlmFallbackInvoked)` (math expr) | `< 0.3` | 1 hour | Ticket — connector regression signal |

All alarms scope on `Schema=RuleExtraction` to avoid mixing with
`PenaltyExtraction` traffic (which uses the same EMF namespace).

## CW Logs Insights queries

The log group is the standard ECS log group (`/fortress/pipeline1` for
the scraper; `/fortress/pipeline2/<bank>` for per-bank P2).

### Find every Bedrock failure with stack:

```
fields @timestamp, schema, error_class, error, regulator
| filter event = "extraction_invoke_failed"
| sort @timestamp desc
| limit 50
```

### Per-regulator fallback rate (last 1h):

```
stats count() as n by event, Regulator
| filter event in ["extraction_regex_fallback", "metric_extraction"]
| filter Regulator like /^.+/
```

### Backfill throughput chart:

```
fields @timestamp, scanned, extracted, written, errors, low_confidence
| filter event = "backfill_done"
| sort @timestamp asc
```

### Token spend (per call, with regulator slice):

```
fields @timestamp, schema, input_tokens, output_tokens, elapsed_ms
| filter event = "extraction_succeeded"
| stats sum(input_tokens) as in_tok, sum(output_tokens) as out_tok,
        avg(elapsed_ms) as p50_ms by bin(1h)
```

## Wiring an alarm via CFN

The existing `infra/cloudformation/pipeline1-compute.yml` already enables
EMF-compatible log delivery (`CloudWatchMetricsEnabled: true`). Add an
alarm resource pointing at the metric — no extra metric filter needed:

```yaml
RuleExtractionBedrockDownAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub "fortress-${Env}-RuleExtraction-BedrockDown"
    Namespace: Fortress/P1/Extraction
    MetricName: BedrockInvokeFailures
    Dimensions:
      - Name: Schema
        Value: RuleExtraction
    Statistic: Sum
    Period: 300
    EvaluationPeriods: 1
    Threshold: 3
    ComparisonOperator: GreaterThanOrEqualToThreshold
    TreatMissingData: notBreaching
    AlarmActions:
      - !ImportValue
          Fn::Sub: "FortressP1-${Env}-SecurityAlertsTopicArn"  # SNS topic w/ email + SMS paging (see docs/runbooks/incident-response.md §4.1)
```

Mirror this pattern for the other three alarm names.

## Rollback / silencing

To silence all extraction telemetry without redeploying: set
`LLM_EXTRACTION_MODE=off`. The Bedrock client is lazy — when mode is
off, no boto3 client is constructed and no metrics fire.
