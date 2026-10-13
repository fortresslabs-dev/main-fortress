#!/usr/bin/env bash
# infra/scripts/encode-app-config-secret.sh
#
# Read fortress/pipeline1/app-config, URL-encode any reserved chars inside the
# DATABASE_URL / REDIS_URL passwords, and write the corrected JSON back.
#
# The data stack renders these URLs with raw `{{resolve:secretsmanager:...}}`
# substitution, which breaks if the generated password contains :, ?, #, +,
# *, &, or =. ExcludeCharacters in SecretDbPassword/SecretRedisToken prevents
# this for *new* secrets, but existing secrets stay as-is — so we run this
# unconditionally after every data-stack deploy as a safety net.
#
# Usage:
#   ./infra/scripts/encode-app-config-secret.sh [secret_name]
#
# Default secret_name: fortress/pipeline1/app-config
set -euo pipefail

SECRET_NAME="${1:-fortress/pipeline1/app-config}"
REGION="${AWS_REGION:-us-east-1}"
PROFILE_ARG=()
if [[ -n "${AWS_PROFILE:-}" ]]; then
  PROFILE_ARG=(--profile "$AWS_PROFILE")
fi

echo "→ Encoding URL passwords in ${SECRET_NAME} ..."

CURRENT=$(aws secretsmanager get-secret-value \
  "${PROFILE_ARG[@]}" --region "$REGION" \
  --secret-id "$SECRET_NAME" --query SecretString --output text)

NEW=$(printf '%s' "$CURRENT" | python3 -c '
import json, sys, urllib.parse as up

cfg = json.loads(sys.stdin.read())

def encode_url_password(url):
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    auth, hostpart = rest.rsplit("@", 1)
    if ":" not in auth:
        return url
    user, pw = auth.split(":", 1)
    decoded = up.unquote(pw)
    encoded = up.quote(decoded, safe="")
    return f"{scheme}://{user}:{encoded}@{hostpart}"

for key in ("DATABASE_URL", "REDIS_URL"):
    if key in cfg:
        cfg[key] = encode_url_password(cfg[key])

sys.stdout.write(json.dumps(cfg))
')

# If python wrote "changed" to stderr, push the update. Otherwise skip.
# (We pipe stderr through tee above? No — simpler: just always push, idempotent.)
aws secretsmanager put-secret-value \
  "${PROFILE_ARG[@]}" --region "$REGION" \
  --secret-id "$SECRET_NAME" \
  --secret-string "$NEW" \
  --query "{VersionId:VersionId}" --output text >/dev/null

echo "  ✓ Wrote URL-encoded passwords back to ${SECRET_NAME}"
