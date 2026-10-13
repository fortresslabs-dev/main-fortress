# 02 — Architecture diagrams

## What goes here

- `p1_architecture_{YYYY-MM-DD}.png` — Pipeline 1 (Fortress Cloud account): VPC, ALB+WAF, ECS Fargate, Aurora pgvector, Redis, Bedrock, 7 VPC endpoints
- `p2_architecture_{YYYY-MM-DD}.png` — Pipeline 2 (per-bank account): VPC, ALB+WAF, ECS Fargate, RDS, Redis, Cognito, Exports S3 (Object Lock COMPLIANCE)
- `p1_p2_data_flow_{YYYY-MM-DD}.png` — cross-account sync flow: P2 UpdateService → HMAC-signed → P1 `/sync/*` endpoints. Highlights the **no-IAM-trust, no-VPC-peering** boundary.
- `tenancy_isolation_{YYYY-MM-DD}.png` — proof diagram for FTR §8 (multi-tenancy isolation). Shows account boundary as the isolation primitive, NOT row-level filtering.
- `kms_key_custody_{YYYY-MM-DD}.png` — shows Fortress has zero grant on bank-owned CMKs

## Source

Diagrams should be regenerated from `infra/cloudformation/*.yml` topology. Suggested tool: AWS Application Composer or [diagrams.net](https://app.diagrams.net/) with AWS icon set. **Source `.drawio` files committed alongside the PNGs** so they stay editable.

## Requirements

- Legible at print A4 / Letter
- AWS official icon set (not third-party)
- Account boundaries drawn as explicit colored rectangles (Fortress account = one color, bank account = another)
- VPC endpoints called out (proves no NAT egress for AWS API calls)
- WAF rules called out (proves edge defence is in place)

## Status (2026-06-26)

⬜ Not started. Highest priority once we open the FTR ticket — the reviewer opens the diagram **first**.
