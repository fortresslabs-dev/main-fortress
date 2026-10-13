# Data Processing Agreement (DPA) — DRAFT TEMPLATE

> **Status:** DRAFT — closes FTR tracker `C3` (partial close of FTR §13.1 B7). **Not legally reviewed.** This template is provided so a bank's counsel can mark it up; the executed DPA is the artifact AWS reviewers expect, not this draft. Final wording must be approved by Fortress Labs counsel + the bank's counsel before execution.
>
> **Companion docs:** FTR §1.3 (shared responsibility), §4 (data protection), §11.4 (customer-data-deletion proof), `docs/runbooks/incident-response.md` (24h notification SLA).
>
> **Version:** v0.1 (2026-06-26).

---

## Data Processing Agreement

**This Data Processing Agreement ("DPA")** supplements and forms part of the Software Subscription Agreement (or AWS Marketplace order, if applicable) (the "Principal Agreement") between:

- **Fortress Labs, Inc.**, a Delaware corporation ("**Processor**" or "**Fortress**"), and
- **[BANK LEGAL NAME]** ("**Controller**" or "**Customer**").

Effective Date: **[YYYY-MM-DD]**.

In the event of any conflict between this DPA and the Principal Agreement with respect to the processing of Customer Personal Data, this DPA controls.

---

### 1. Definitions

- **"Customer Personal Data"** means any personal data (as defined under Applicable Data Protection Law) that Fortress Processes on behalf of Customer in connection with the Service.
- **"Applicable Data Protection Law"** means all data protection and privacy laws applicable to the Processing of Customer Personal Data under the Principal Agreement, including (where applicable) the EU GDPR, UK GDPR, CCPA/CPRA, GLBA, and state-level US financial-privacy regulations.
- **"Service"** means Fortress Labs Pipelines 1 and 2 (regulatory compliance SaaS).
- **"Sub-processor"** means any third party engaged by Fortress to Process Customer Personal Data on Customer's behalf.
- **"Personal Data Breach"** has the meaning set out in the applicable law.
- All other capitalized terms have the meanings given in the Principal Agreement or the Applicable Data Protection Law.

---

### 2. Subject matter, nature and purpose of Processing

| Item | Description |
|---|---|
| **Subject matter** | Provision of the Service: detection of regulatory change, validation of bank operational events against regulatory rules, generation of compliance test code and board-level reports. |
| **Nature of Processing** | Storage, retrieval, indexing, vector-embedding, BM25 ranking, AI-assisted summarization (Amazon Bedrock), validation against rule thresholds, generation of reports and JIRA tickets. |
| **Purpose** | Enable Customer to meet its compliance obligations (BSA/AML, OFAC, FFIEC IT Hdbk, state banking law) under its primary regulator's oversight. |
| **Duration** | Term of the Principal Agreement + the deprovisioning data-export window (30 days) + any extended retention required by Applicable Data Protection Law or Customer's own record-retention policy. |
| **Categories of data subjects** | (i) Customer's employees with Service access (CCO, CRO, CIO, analysts), (ii) the natural-person counterparties of Customer's banking activity (wire originators / beneficiaries, KYC-screened individuals), only to the extent required by the relevant regulatory rule (e.g. OFAC name screening). |
| **Categories of Customer Personal Data** | Name, account / wire identifiers, OFAC match metadata, KYC fields as supplied by Customer, internal employee identifiers for audit. Fortress does NOT receive raw card-PAN, biometric, or health data. |
| **Special categories** | None expected. If Customer transmits any, Fortress will treat them under the same controls as set out in §4. |

---

### 3. Roles and lawful basis

3.1. **Customer is the Controller** of Customer Personal Data. Fortress is the **Processor**.

3.2. Customer represents and warrants that (i) it has a lawful basis for its Processing of Customer Personal Data, (ii) it has provided required notices to data subjects, and (iii) it has obtained all consents required by Applicable Data Protection Law.

3.3. Fortress shall Process Customer Personal Data only on documented instructions from Customer, including with regard to transfers to a third country, unless required to do so by law applicable to Fortress. In any such case, Fortress will inform Customer of the legal requirement before Processing, unless that law prohibits such information.

3.4. **Architectural note (reflects §1.3 of `docs/aws_ftr_marketplace_p1_p2.md`).** Customer Personal Data is processed exclusively within **Pipeline 2**, which is deployed into Customer's own AWS account. Fortress's AWS account (Pipeline 1) holds only public regulatory text. Customer retains custody of all KMS keys protecting Customer Personal Data; Fortress holds zero grant on those keys.

---

### 4. Fortress's security obligations

Fortress shall implement and maintain appropriate technical and organisational measures to ensure a level of security appropriate to the risk, including:

4.1. **Encryption** at rest using bank-owned KMS CMKs (RDS, Redis, exports S3) and in transit via TLS 1.2+ for all data flows.

4.2. **Access control** via per-bank Cognito user pool (12+ character password policy, MFA on privileged groups), AWS-managed IAM roles for service-to-service access, no IAM users for application access.

4.3. **Network isolation** via per-bank VPC with private subnets for compute and data tiers, ALB+WAF on the only public ingress, no VPC peering between Fortress and Customer accounts.

4.4. **Audit logging** via Customer-owned CloudTrail (data-plane custody remains with Customer), Fortress-side application logs in CloudWatch with 365-day retention (FFIEC alignment).

4.5. **Vulnerability management** via dependency scanning on every code change (`pip-audit`, `npm audit`), container image scanning (Inspector), and an annual third-party penetration test.

4.6. **Incident detection** via GuardDuty (account-level) and Security Hub (PCI DSS v3.2.1 + AWS FSBP v1.0.0 standards).

4.7. **Personnel** with access to Customer Personal Data: (i) are subject to confidentiality obligations, (ii) receive annual security training, (iii) have access provisioned on a least-privilege basis and revoked within 24 hours of role change or departure.

4.8. Fortress shall provide reasonable evidence of the above on Customer's written request (no more than once per twelve-month period absent a Personal Data Breach), including the most recent SOC 2 Type II report when available.

---

### 5. Personal Data Breach notification

5.1. Fortress shall notify Customer **without undue delay and in any event within 24 hours** of becoming aware of a Personal Data Breach affecting Customer Personal Data (per `docs/runbooks/incident-response.md` §6).

5.2. The notification shall describe, to the extent known at the time:

- The nature of the Personal Data Breach, including where possible the categories and approximate number of data subjects and records concerned;
- The likely consequences;
- The measures taken or proposed to address the Personal Data Breach and mitigate its possible adverse effects.

5.3. Fortress shall cooperate with Customer's reasonable requests for additional information and assistance with respect to the Personal Data Breach, including for the purpose of Customer's own regulatory notification obligations (e.g. state breach-notification statutes, FFIEC computer-security incident notification).

5.4. Fortress shall provide a final post-incident report within ten (10) business days of incident resolution.

---

### 6. Sub-processors

6.1. Customer provides **general written authorisation** for Fortress to engage Sub-processors. The current list of Sub-processors is:

| Sub-processor | Purpose | Location |
|---|---|---|
| Amazon Web Services, Inc. | Infrastructure (compute, storage, database, KMS, networking, AI inference via Bedrock) | us-east-1 |
| _[Add: payment / Marketplace billing processor when integrated]_ | _Marketplace billing_ | _TBD_ |
| _[None — paging is AWS-native (SNS-SMS), no external Sub-processor]_ | _Alerting / on-call_ | _n/a_ |
| _[Add: support-desk vendor if/when adopted]_ | _Support ticketing_ | _TBD_ |

6.2. Fortress shall maintain an up-to-date list of Sub-processors at `docs/legal/sub-processors.md` (TBD) and shall provide at least **thirty (30) days' notice** before adding or replacing any Sub-processor.

6.3. Customer may object to Fortress's appointment of a new Sub-processor on reasonable grounds within thirty (30) days of notice. If the parties cannot agree on a resolution, Customer may terminate the Principal Agreement with respect to those Services that cannot be provided by Fortress without the new Sub-processor, without penalty.

6.4. Fortress shall enter into a written agreement with each Sub-processor imposing data-protection obligations no less protective than those in this DPA.

---

### 7. Data subject rights

7.1. Fortress shall, to the extent legally permitted, promptly notify Customer if Fortress receives a request from a data subject to exercise rights under Applicable Data Protection Law (access, rectification, erasure, restriction, portability, objection).

7.2. Fortress shall not respond to such a request directly, except on Customer's documented instructions or as required by Applicable Data Protection Law.

7.3. Fortress shall provide reasonable assistance to enable Customer to fulfil its obligations to respond to such requests. **Architectural note.** Because Customer Personal Data resides in Customer's own AWS account, Customer can fulfil most data-subject requests directly without Fortress involvement.

---

### 8. International transfers

8.1. The Service is operated from **AWS region us-east-1**. Fortress does not currently transfer Customer Personal Data outside the United States, except for Bedrock cross-region inference routing within the `us.*` inference-profile pool (US-only).

8.2. If Customer requires data residency in another AWS region, Fortress will quote a separate engagement covering the additional infrastructure cost.

8.3. Where the EU GDPR or UK GDPR applies and transfers occur, the parties agree to incorporate the relevant Standard Contractual Clauses ("SCCs") by reference, with the modules and annexes to be agreed in writing.

---

### 9. Audits

9.1. Fortress shall make available to Customer all information necessary to demonstrate compliance with this DPA, and shall allow for and contribute to audits, including inspections, conducted by Customer or another auditor mandated by Customer.

9.2. Customer's audit right is satisfied by Fortress providing, on request:

- The most recent SOC 2 Type II report (when available — target Q4 2026 per FTR §12.1).
- The current AWS Foundational Technical Review status (this template's parent project).
- Annual third-party penetration test summary.
- Evidence of the security controls in §4 (CFN templates, screenshots from `docs/ftr_evidence/`).

9.3. On-site audits by Customer are permitted no more than once per twelve-month period, on at least thirty (30) days' written notice, during normal business hours, subject to confidentiality undertakings, and limited to information necessary to verify Fortress's compliance with this DPA.

---

### 10. Return and deletion

10.1. Upon termination of the Principal Agreement, Fortress shall, at Customer's choice, return or delete all Customer Personal Data in its custody, except to the extent retention is required by law.

10.2. **Architectural note.** Customer Personal Data resides in Customer's AWS account. Deletion is performed by Customer's IT team executing `infra/scripts/deprovision-p2.sh` and, when ready, deleting the Customer-owned KMS CMK (30-day pending-deletion window). Once the CMK is deleted, all WORM-retained exports become **cryptographically destroyed**. Fortress cannot reverse or prevent this.

10.3. Records retained by Fortress (Pipeline 1 only) after termination, on a legitimate-interest basis, are limited to:

- AWS Marketplace subscription metadata (account ID, contract ID, dates);
- Aggregate billing metrics (sync count, artifact count — no content);
- Support ticket history (subject + non-sensitive body);
- Audit trail of HMAC key issuances / rotations.

These categories contain no Customer Personal Data as defined in §1.

10.4. Fortress shall provide written confirmation of deletion within thirty (30) days of Customer's written request.

---

### 11. Liability and limitations

11.1. Each party's liability arising out of or in connection with this DPA shall be subject to the limitations and exclusions of liability set out in the Principal Agreement.

11.2. Nothing in this DPA limits either party's liability for personal injury, fraud, or any other liability that cannot be excluded by Applicable Data Protection Law.

---

### 12. General

12.1. **Order of precedence.** In the event of a conflict between this DPA and the Principal Agreement regarding Processing of Customer Personal Data, this DPA controls. In the event of a conflict between this DPA and the SCCs (where applicable), the SCCs control.

12.2. **Governing law and jurisdiction.** As set out in the Principal Agreement, except where Applicable Data Protection Law requires otherwise.

12.3. **Modifications.** Any amendment to this DPA must be in writing and signed by both parties.

12.4. **Severability.** If any provision of this DPA is held to be invalid, the remainder shall continue in full force.

---

**EXECUTED** by the duly authorised representatives of the parties:

```
For Fortress Labs, Inc.                    For [BANK LEGAL NAME]
─────────────────────────                  ─────────────────────────
Name:                                       Name:
Title:                                      Title:
Date:                                       Date:
```

---

## Annex A — Technical and Organisational Measures (summary)

Drawn from `docs/aws_ftr_marketplace_p1_p2.md` §4 and §5. Detailed evidence in `docs/ftr_evidence/03_security_tools/`.

| Domain | Measure |
|---|---|
| Encryption at rest | KMS CMKs (bank-owned for Customer Personal Data); annual rotation enabled |
| Encryption in transit | TLS 1.2+ ALB / CloudFront; `aws:SecureTransport: true` on S3 bucket policies |
| Access control | Cognito (per-bank, MFA on privileged groups), IAM roles for service-to-service, no IAM users for app |
| Network | Private subnets for compute + data; WAFv2 on ALB; 7 VPC endpoints (no NAT for AWS APIs) |
| Logging | Customer CloudTrail (Customer custody), CloudWatch Logs (Fortress + Customer), CloudTrail log-file validation |
| Monitoring | GuardDuty (runtime + RDS login), Security Hub (PCI DSS + AWS FSBP), CloudWatch alarms |
| Backup | RDS continuous + daily snapshot; Exports S3 Object Lock COMPLIANCE 1825 days |
| Vulnerability mgmt | dependabot + `pip-audit` + `npm audit` + ECR Inspector + annual third-party pen test |
| Personnel | Confidentiality obligations + annual training + least-privilege access |
| Incident response | 24h Customer notification SLA per `docs/runbooks/incident-response.md` |

---

## Annex B — Sub-processor list

Maintained at `docs/legal/sub-processors.md` (TBD). See §6.1 for the current snapshot.
