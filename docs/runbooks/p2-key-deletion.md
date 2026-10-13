# Pipeline 2 — KMS Key Deletion Runbook

> **Status:** Closes the Eng-side follow-on from FTR §13.1 `B7` — the "scripted
> KMS-key-pending-deletion proof" referenced in `infra/scripts/deprovision-p2.sh`
> (header comment, step 10, and summary). With this runbook in place,
> B7 is reduced to **Legal-only** (DPA sign-off).
>
> **Closes FTR tracker:** Eng follow-on of `C2`.
> **Last reviewed:** 2026-06-30 · **Cadence:** Per-bank, executed once at end of
> the deprovisioning window.

---

## 1. Why this runbook exists

`infra/scripts/deprovision-p2.sh` tears down everything Fortress provisioned in
the bank's AWS account *except* the resources that hold immutable audit
evidence:

| Resource | Why it survives `deprovision-p2.sh` |
|---|---|
| Exports S3 bucket (`fortress-p2-exports-{bank}-{env}`) | Object Lock COMPLIANCE 1825d — even root cannot delete inside the window |
| Exports KMS CMK (`alias/fortress-p2-exports-{bank}-{env}`) | `DeletionPolicy: Retain` on the CFN resource — CFN never deletes it |
| Final RDS snapshot (`{db-id}-final-YYYYMMDD-HHMM`) | Tagged with `RetainUntil` — bank policy decides when to drop it |

The other two CMKs (RDS and Redis encryption keys, owned by the
`fortress-p2-data-{bank}` stack) are scheduled for deletion **automatically**
when the data stack is torn down in step 7 of the deprovision script. The
exports CMK is the one that requires manual action — and the one whose
`PendingDeletion` state we need to capture as proof for the FTR audit.

This runbook covers:

- Listing the surviving bank-owned CMKs after deprovision.
- Scheduling the exports CMK for deletion (or canceling, if the bank changes
  its mind inside the 30-day window).
- Capturing the `KeyState: PendingDeletion` + `DeletionDate` proof for the FTR
  evidence pack.
- Cryptographic-destruction implications for the WORM exports bucket.

---

## 2. Bank-owned KMS CMK inventory

Per the FTR §1.3 shared-responsibility matrix, these keys are **bank-owned**.
Fortress has zero access (key policies grant `kms:*` only to the bank account
root). Fortress publishes this runbook; the bank IT team executes against
their own account.

| Alias | Owning CFN stack | DeletionPolicy | Auto-deletes with stack? | Pending window |
|---|---|---|---|---|
| `alias/fortress-rds-{bank}` | `fortress-p2-data-{bank}` | (CFN default — Delete) | ✅ scheduled at stack delete | 30d (AWS default) |
| `alias/fortress-redis-{bank}` | `fortress-p2-data-{bank}` | (CFN default — Delete) | ✅ scheduled at stack delete | 30d (AWS default) |
| `alias/fortress-p2-exports-{bank}-{env}` | `fortress-p2-exports-bucket-{bank}-{env}` | **Retain** | ❌ requires manual schedule-key-deletion | 30d (template-pinned, `PendingWindowInDays: 30`) |

> **Reality check:** Run `aws kms list-aliases --region us-east-1 --query \
> "Aliases[?starts_with(AliasName, 'alias/fortress-')].AliasName" --output text`
> after deprovision. You should see the rds/redis aliases gone (orphaned aliases
> auto-delete when their target key is scheduled for deletion) and only
> `alias/fortress-p2-exports-{bank}-{env}` remaining. If the rds/redis aliases
> are still present, the data stack delete didn't actually complete — go back
> and verify in CFN before continuing.

---

## 3. Pre-flight — confirm deprovision is complete

```bash
BANK_ID=<bank-slug>
ENVIRONMENT=production
REGION=us-east-1

# 1) All P2 stacks (except exports-bucket) should be gone.
aws cloudformation describe-stacks \
  --region "$REGION" \
  --query "Stacks[?starts_with(StackName, 'fortress-p2-') && contains(StackName, '$BANK_ID')].[StackName,StackStatus]" \
  --output table
#   Expected: only fortress-p2-exports-bucket-${BANK_ID}-${ENVIRONMENT} remains.

# 2) The exports CMK should still be Enabled (not yet PendingDeletion).
aws kms describe-key \
  --key-id "alias/fortress-p2-exports-${BANK_ID}-${ENVIRONMENT}" \
  --region "$REGION" \
  --query "KeyMetadata.[KeyId,KeyState,DeletionDate,PendingWindowInDays]" \
  --output table

# 3) The exports bucket's earliest unexpired Object Lock retention date.
#    KMS deletion BEFORE this date = cryptographic destruction of audit
#    evidence inside the retention window. Get bank Legal sign-off first.
BUCKET="fortress-p2-exports-${BANK_ID}-${ENVIRONMENT}"
aws s3api list-object-versions --bucket "$BUCKET" --output json | \
  jq -r '.Versions[]? | [.Key, .VersionId] | @tsv' | head -5 | \
  while read key vid; do
    aws s3api get-object-retention --bucket "$BUCKET" --key "$key" \
      --version-id "$vid" --query 'Retention.RetainUntilDate' --output text
  done | sort -u | head
```

If step 3 returns retention dates in the future, **STOP** and route to bank
Legal. Scheduling key deletion now will render those objects unreadable when
the pending window expires (30d from schedule), even though Object Lock
COMPLIANCE would otherwise have preserved them.

---

## 4. Schedule the exports CMK for deletion

There are two paths. Both produce the same FTR-acceptable proof.

### 4.1 Direct AWS CLI (preferred — auditable, one command)

```bash
BANK_ID=<bank-slug>
ENVIRONMENT=production
REGION=us-east-1
TICKET_ID=<deprovisioning-ticket>          # e.g. SEC-2026-04711
KEY_ID=$(aws kms describe-key \
  --key-id "alias/fortress-p2-exports-${BANK_ID}-${ENVIRONMENT}" \
  --region "$REGION" \
  --query 'KeyMetadata.KeyId' --output text)

# Schedule for deletion. 30 days is the template default; the bank may extend
# to 30 (max for KMS) or use a shorter window down to 7d if Legal approves.
aws kms schedule-key-deletion \
  --key-id "$KEY_ID" \
  --pending-window-in-days 30 \
  --region "$REGION" \
  --output json
```

Expected response (record this verbatim in the FTR evidence pack):

```json
{
  "KeyId": "arn:aws:kms:us-east-1:<bank-acct>:key/<uuid>",
  "DeletionDate": "2026-07-30T00:00:00+00:00",
  "KeyState": "PendingDeletion",
  "PendingWindowInDays": 30
}
```

Tag the key with the ticket and the bank ID so the audit trail is complete
even after deletion:

```bash
aws kms tag-resource \
  --key-id "$KEY_ID" \
  --tags "TagKey=DeprovisionTicket,TagValue=${TICKET_ID}" \
         "TagKey=BankId,TagValue=${BANK_ID}" \
         "TagKey=ScheduledBy,TagValue=$(aws sts get-caller-identity --query Arn --output text)" \
  --region "$REGION"
```

### 4.2 Via `deprovision-p2.sh` step 10

The script auto-invokes this runbook's procedure when `KEY_DELETION_MODE=execute`
is set. Default mode is `schedule` (print-only — surfaces the exact CLI command
without firing it, so an auditor can run it independently). Default behaviour
preserves the "Fortress has no access" property of the key.

```bash
# Print the exact schedule-key-deletion command + the verification command
# (no AWS-state change). Always safe to run.
./infra/scripts/deprovision-p2.sh "${BANK_ID}" "${ENVIRONMENT}"

# Execute schedule-key-deletion as the bank IT operator (requires LIVE mode +
# the explicit gate below).
CONFIRM="DELETE-${BANK_ID}" \
DEPROVISION_TICKET_ID="${TICKET_ID}" \
KEY_DELETION_MODE=execute \
  ./infra/scripts/deprovision-p2.sh "${BANK_ID}" "${ENVIRONMENT}"
```

`KEY_DELETION_MODE=execute` requires LIVE mode + a ticket ID; the script
refuses to run otherwise.

---

## 5. Capture the proof for FTR

The "proof" the FTR submission needs is a recorded `describe-key` showing
`KeyState: PendingDeletion` and a future `DeletionDate`. Capture it
immediately after step 4, save to the FTR evidence drive, and reference it in
the §13.1 B7 close-out.

```bash
aws kms describe-key \
  --key-id "$KEY_ID" \
  --region "$REGION" \
  --query "KeyMetadata" \
  --output json | tee "evidence/ftr-b7-key-deletion-${BANK_ID}-$(date -u +%Y%m%d).json"
```

Required fields in the captured JSON (auditor checks these):

- `KeyState` = `"PendingDeletion"`
- `DeletionDate` present, ISO 8601, ≥7 days in the future
- `PendingWindowInDays` matches what `schedule-key-deletion` recorded
- `Description` still references the bank (sanity check we deleted the right
  key)

The same `describe-key` should appear in CloudTrail under the bank's audit
trail — record the `eventID` of the `ScheduleKeyDeletion` event alongside the
JSON.

---

## 6. Cancel deletion (rollback inside the pending window)

If anyone — bank Legal, the bank's regulator, a re-onboarding decision —
needs the data back before the pending window expires, **act before
`DeletionDate`**:

```bash
aws kms cancel-key-deletion \
  --key-id "$KEY_ID" \
  --region "$REGION"

# Re-enable the key (cancel-key-deletion leaves it Disabled).
aws kms enable-key \
  --key-id "$KEY_ID" \
  --region "$REGION"

# Re-create the alias if it was removed.
aws kms create-alias \
  --alias-name "alias/fortress-p2-exports-${BANK_ID}-${ENVIRONMENT}" \
  --target-key-id "$KEY_ID" \
  --region "$REGION"
```

After `DeletionDate` passes, the key is **unrecoverable** and every object in
the WORM bucket encrypted under it is cryptographically destroyed — Object
Lock COMPLIANCE notwithstanding. There is no AWS-side recovery path.

---

## 7. Cryptographic destruction & the WORM bucket

The exports bucket (`fortress-p2-exports-{bank}-{env}`) uses Object Lock
COMPLIANCE mode with a default 5-year retention. The bucket policy refuses
`PUT` without `aws:kms` server-side encryption referencing this exact CMK
(see `infra/cloudformation/pipeline2-exports-bucket.yml` — `DenyPutWithoutKMS`).

The compliance posture is:

- **Inside the pending window (default 30d):** key still usable; objects
  remain readable; `cancel-key-deletion` restores the status quo.
- **After `DeletionDate`:** key gone; SSE-KMS objects cannot be decrypted by
  anyone (not even AWS); Object Lock still prevents deletion of the
  ciphertext, but the plaintext is gone forever. This is the **explicit
  cryptographic-destruction path** FTR §13.1 B7 references.

Use this path **only** when:

1. The contractual retention has expired, OR
2. Bank Legal has signed off on early cryptographic destruction (record the
   sign-off in the FTR evidence pack), OR
3. A regulator has ordered destruction.

For (1), the earliest valid `DeletionDate` is `bucket_creation + 1825d + 30d`.

---

## 8. The other two CMKs — RDS and Redis

`alias/fortress-rds-{bank}` and `alias/fortress-redis-{bank}` are owned by
`fortress-p2-data-{bank}`. They do not have `DeletionPolicy: Retain`, so
CloudFormation schedules their deletion automatically when step 7 of
`deprovision-p2.sh` deletes the data stack. AWS schedules them with the
default 30-day pending window (KMS's minimum-permissible). The CFN
`DELETE_COMPLETE` event for the data stack is the FTR proof that these keys
moved to `PendingDeletion` — no separate `schedule-key-deletion` call is
needed.

If the data-stack delete failed for any reason (e.g. the RDS instance had
existing snapshots that blocked the implicit final-snapshot policy), the keys
will still exist. In that case, follow §4 against each key alias — but **STOP
first** and figure out *why* the stack delete failed; orphan keys after a
botched delete usually mean orphan resources elsewhere that need attention.

---

## 9. Audit trail

Every action above lands in the bank's CloudTrail. Required events to capture
for the FTR evidence pack:

| Event | Source | Maps to |
|---|---|---|
| `ScheduleKeyDeletion` | KMS | §4 — proves pending-deletion was initiated |
| `TagResource` | KMS | §4 — proves ticket linkage |
| `DescribeKey` | KMS | §5 — proves we captured the proof from a valid AWS API call |
| `DeleteStack: fortress-p2-data-{bank}` | CloudFormation | §8 — proves rds/redis CMKs followed the stack |
| `CancelKeyDeletion` (if applicable) | KMS | §6 — proves rollback was honored |

Pull the events with:

```bash
aws cloudtrail lookup-events \
  --region us-east-1 \
  --lookup-attributes AttributeKey=ResourceName,AttributeValue="$KEY_ID" \
  --start-time "$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)" \
  --query "Events[].{Time:EventTime,Name:EventName,User:Username}" \
  --output table
```

---

## 10. Edge cases & pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `schedule-key-deletion` returns `AccessDeniedException` | Caller is not in bank account root or is missing `kms:ScheduleKeyDeletion` | Have a bank account admin run it; Fortress has no access by design |
| `describe-key` returns `KeyState: Disabled` after cancel | `cancel-key-deletion` leaves keys Disabled | Run `aws kms enable-key` (§6) |
| Alias missing after stack delete | CFN deletes aliases atomically with the key | Re-create with `create-alias` only if rolling back; otherwise leave deleted |
| RDS/Redis CMKs still present after data stack delete | Data stack delete failed silently or alias points to wrong key | Re-check `describe-stacks` for `DELETE_FAILED`; resolve resource conflict, retry delete |
| Pending window appears as 7 in `describe-key` but template says 30 | Bank IT explicitly overrode the window at schedule time; the template value is only the default | This is acceptable; capture the actual value in the FTR proof JSON |
| Cannot read exports bucket objects after `DeletionDate` | Cryptographic destruction has occurred (expected) | This is the intended behaviour. Object Lock preserves ciphertext; plaintext is gone. Not a bug. |
