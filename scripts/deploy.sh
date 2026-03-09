#!/usr/bin/env bash
# deploy.sh — Zero-downtime re-deploy for subsequent releases.
# Run on the EC2 instance after new images have been pushed to ECR.
#
# Usage:
#   ssh ubuntu@<ec2-host> 'bash ~/energypulse/scripts/deploy.sh'
#
# Required env vars (already in /home/ubuntu/energypulse/.env on the instance):
#   AWS_ACCOUNT_ID, AWS_REGION, ECR_BACKEND_URI, ECR_FRONTEND_URI
#   (All other vars are loaded from .env automatically.)

set -euo pipefail

APP_DIR="/home/ubuntu/energypulse"
COMPOSE_FILE="${APP_DIR}/docker-compose.prod.yml"
ENV_FILE="${APP_DIR}/.env"

# ─── Load .env so ECR vars are available in this shell ────────────────────────
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} not found. Run setup_ec2.sh first." >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a; source "${ENV_FILE}"; set +a

# ─── Validate ECR vars ────────────────────────────────────────────────────────
for VAR in AWS_ACCOUNT_ID AWS_REGION ECR_BACKEND_URI ECR_FRONTEND_URI; do
  if [[ -z "${!VAR:-}" ]]; then
    echo "ERROR: '${VAR}' is not set in ${ENV_FILE}." >&2
    exit 1
  fi
done

ECR_BASE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# ─── Step 1: Re-authenticate to ECR ──────────────────────────────────────────
echo ""
echo "==> [1/4] Authenticating Docker with ECR..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_BASE_URI}"
echo "    Authenticated."

# ─── Step 2: Pull new images ──────────────────────────────────────────────────
echo ""
echo "==> [2/4] Pulling latest images from ECR..."
cd "${APP_DIR}"
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" pull
echo "    Images pulled."

# ─── Step 3: Recreate containers with new images ─────────────────────────────
echo ""
echo "==> [3/4] Restarting stack with new images..."
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" up -d --no-build
echo "    Stack restarted."

# ─── Step 4: Prune dangling images ────────────────────────────────────────────
echo ""
echo "==> [4/4] Pruning dangling images to free disk space..."
docker image prune -f
echo "    Pruned."

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "========================================="
echo " Deploy complete!"
echo "========================================="
echo " Backend:   ${ECR_BACKEND_URI}"
echo " Frontend:  ${ECR_FRONTEND_URI}"
echo ""
echo " Running containers:"
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
echo "========================================="
