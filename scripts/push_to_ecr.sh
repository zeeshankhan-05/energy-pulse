#!/usr/bin/env bash
set -euo pipefail

# ─── Validate required env vars ───────────────────────────────────────────────
if [[ -z "${AWS_ACCOUNT_ID:-}" ]]; then
  echo "ERROR: AWS_ACCOUNT_ID is not set. Export it before running this script." >&2
  exit 1
fi

if [[ -z "${AWS_REGION:-}" ]]; then
  echo "ERROR: AWS_REGION is not set. Export it before running this script." >&2
  exit 1
fi

# ─── Derived vars ─────────────────────────────────────────────────────────────
GIT_SHA=$(git rev-parse --short HEAD)
ECR_BASE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "==> Git SHA:       ${GIT_SHA}"
echo "==> ECR base URI:  ${ECR_BASE_URI}"
echo ""

# ─── Ensure ECR repositories exist ───────────────────────────────────────────
for REPO in energypulse-backend energypulse-frontend; do
  echo "==> Checking ECR repository: ${REPO}"
  if ! aws ecr describe-repositories \
        --repository-names "${REPO}" \
        --region "${AWS_REGION}" \
        > /dev/null 2>&1; then
    echo "    Repository not found — creating ${REPO}..."
    aws ecr create-repository \
      --repository-name "${REPO}" \
      --region "${AWS_REGION}" \
      > /dev/null
    echo "    Created: ${REPO}"
  else
    echo "    Already exists: ${REPO}"
  fi
done

# ─── Authenticate Docker with ECR ─────────────────────────────────────────────
echo ""
echo "==> Authenticating Docker with ECR..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_BASE_URI}"

# ─── Build backend ────────────────────────────────────────────────────────────
echo ""
echo "==> Building backend image..."
docker build -f backend/Dockerfile.prod -t backend_local ./backend

BACKEND_LATEST="${ECR_BASE_URI}/energypulse-backend:latest"
BACKEND_SHA="${ECR_BASE_URI}/energypulse-backend:${GIT_SHA}"

docker tag backend_local "${BACKEND_LATEST}"
docker tag backend_local "${BACKEND_SHA}"

# ─── Build frontend ───────────────────────────────────────────────────────────
echo ""
echo "==> Building frontend image..."
docker build -f frontend/Dockerfile.prod -t frontend_local ./frontend

FRONTEND_LATEST="${ECR_BASE_URI}/energypulse-frontend:latest"
FRONTEND_SHA="${ECR_BASE_URI}/energypulse-frontend:${GIT_SHA}"

docker tag frontend_local "${FRONTEND_LATEST}"
docker tag frontend_local "${FRONTEND_SHA}"

# ─── Push all four tags ───────────────────────────────────────────────────────
echo ""
echo "==> Pushing images to ECR..."
docker push "${BACKEND_LATEST}"
docker push "${BACKEND_SHA}"
docker push "${FRONTEND_LATEST}"
docker push "${FRONTEND_SHA}"

# ─── Success summary ──────────────────────────────────────────────────────────
echo ""
echo "========================================="
echo " Push complete!"
echo "========================================="
echo " Git SHA:  ${GIT_SHA}"
echo ""
echo " Backend:"
echo "   ${BACKEND_LATEST}"
echo "   ${BACKEND_SHA}"
echo ""
echo " Frontend:"
echo "   ${FRONTEND_LATEST}"
echo "   ${FRONTEND_SHA}"
echo "========================================="
