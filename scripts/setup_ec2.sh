#!/usr/bin/env bash
# setup_ec2.sh — One-time provisioning for a fresh Ubuntu 24.04 EC2 instance.
# Run as root (e.g. via EC2 user-data) or with sudo privileges.
#
# Required env vars:
#   AWS_ACCOUNT_ID, AWS_REGION
#   ECR_BACKEND_URI, ECR_FRONTEND_URI
#   DATABASE_URL, REDIS_URL, POSTGRES_PASSWORD, POSTGRES_USER, POSTGRES_DB
#   ANTHROPIC_API_KEY, EIA_API_KEY, SECRET_KEY
#   SENDGRID_API_KEY (optional), SLACK_WEBHOOK_URL (optional)
#
# NOTE — nginx/docker port conflict:
#   The frontend container binds host port 80. If you want host nginx to act
#   as an SSL terminator (recommended for HTTPS), change the frontend ports
#   in docker-compose.prod.yml to "8080:80" and configure nginx to proxy
#   localhost:8080. Certbot will then handle certs via the host nginx.

set -euo pipefail

# ─── Validate required env vars ───────────────────────────────────────────────
REQUIRED_VARS=(
  AWS_ACCOUNT_ID AWS_REGION
  ECR_BACKEND_URI ECR_FRONTEND_URI
  DATABASE_URL REDIS_URL
  POSTGRES_PASSWORD POSTGRES_USER POSTGRES_DB
  ANTHROPIC_API_KEY EIA_API_KEY SECRET_KEY
)
for VAR in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!VAR:-}" ]]; then
    echo "ERROR: Required environment variable '${VAR}' is not set." >&2
    exit 1
  fi
done

APP_DIR="/home/ubuntu/energypulse"
COMPOSE_FILE="${APP_DIR}/docker-compose.prod.yml"
ENV_FILE="${APP_DIR}/.env"

# ─── Step 1: System packages ──────────────────────────────────────────────────
echo ""
echo "==> [1/9] Updating apt and installing packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  docker.io \
  docker-compose-plugin \
  awscli \
  nginx \
  certbot \
  python3-certbot-nginx
systemctl enable docker
systemctl start docker
echo "    Packages installed."

# ─── Step 2: Add ubuntu user to docker group ──────────────────────────────────
echo ""
echo "==> [2/9] Adding ubuntu user to docker group..."
usermod -aG docker ubuntu
echo "    Done. User 'ubuntu' added to docker group."
echo "    NOTE: group change takes effect on next login / newgrp docker."

# ─── Step 3: Create application directory ────────────────────────────────────
echo ""
echo "==> [3/9] Creating application directory at ${APP_DIR}..."
mkdir -p "${APP_DIR}"
chown ubuntu:ubuntu "${APP_DIR}"
echo "    Created ${APP_DIR}."

# ─── Step 4: Copy docker-compose.prod.yml ────────────────────────────────────
echo ""
echo "==> [4/9] Copying docker-compose.prod.yml..."
# Expects docker-compose.prod.yml to be in the same directory as this script,
# or already transferred to the instance before running setup.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/../docker-compose.prod.yml" ]]; then
  cp "${SCRIPT_DIR}/../docker-compose.prod.yml" "${COMPOSE_FILE}"
elif [[ -f "/tmp/docker-compose.prod.yml" ]]; then
  cp "/tmp/docker-compose.prod.yml" "${COMPOSE_FILE}"
else
  echo "ERROR: docker-compose.prod.yml not found. Copy it to /tmp/ before running setup." >&2
  exit 1
fi
chown ubuntu:ubuntu "${COMPOSE_FILE}"
echo "    Copied to ${COMPOSE_FILE}."

# ─── Step 5: Write .env file ──────────────────────────────────────────────────
echo ""
echo "==> [5/9] Writing .env file to ${ENV_FILE}..."
cat > "${ENV_FILE}" <<EOF
DATABASE_URL=${DATABASE_URL}
REDIS_URL=${REDIS_URL}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_DB=${POSTGRES_DB}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
EIA_API_KEY=${EIA_API_KEY}
SECRET_KEY=${SECRET_KEY}
SENDGRID_API_KEY=${SENDGRID_API_KEY:-}
SLACK_WEBHOOK_URL=${SLACK_WEBHOOK_URL:-}
ECR_BACKEND_URI=${ECR_BACKEND_URI}
ECR_FRONTEND_URI=${ECR_FRONTEND_URI}
AWS_REGION=${AWS_REGION}
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID}
EOF
chmod 600 "${ENV_FILE}"
chown ubuntu:ubuntu "${ENV_FILE}"
echo "    Written and locked to 600."

# ─── Step 6: Authenticate Docker to ECR ──────────────────────────────────────
echo ""
echo "==> [6/9] Authenticating Docker with ECR (region: ${AWS_REGION})..."
ECR_BASE_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_BASE_URI}"
echo "    Docker authenticated to ECR."

# ─── Step 7: Pull latest images from ECR ─────────────────────────────────────
echo ""
echo "==> [7/9] Pulling images from ECR..."
docker pull "${ECR_BACKEND_URI}"
docker pull "${ECR_FRONTEND_URI}"
echo "    Images pulled."

# ─── Step 8: Start the stack ──────────────────────────────────────────────────
echo ""
echo "==> [8/9] Starting docker compose stack..."
cd "${APP_DIR}"
sudo -u ubuntu docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" up -d --no-build
echo "    Stack is up."

# ─── Step 9: Install and enable systemd service ───────────────────────────────
echo ""
echo "==> [9/9] Installing energypulse.service systemd unit..."
cat > /etc/systemd/system/energypulse.service <<EOF
[Unit]
Description=EnergyPulse Docker Compose Stack
Documentation=https://github.com/your-org/energy-pulse
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
# Run as ubuntu so the docker group membership applies
User=ubuntu
Group=ubuntu
ExecStartPre=/usr/bin/docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} pull --quiet
ExecStart=/usr/bin/docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} up -d --no-build
ExecStop=/usr/bin/docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} down
# On failure, wait 10 s then retry
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable energypulse.service
systemctl start energypulse.service
echo "    energypulse.service enabled and started."

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "========================================="
echo " EnergyPulse setup complete!"
echo "========================================="
echo " App directory:  ${APP_DIR}"
echo " Compose file:   ${COMPOSE_FILE}"
echo " Env file:       ${ENV_FILE}"
echo " ECR backend:    ${ECR_BACKEND_URI}"
echo " ECR frontend:   ${ECR_FRONTEND_URI}"
echo ""
echo " Next steps:"
echo "   - Configure SSL: certbot --nginx -d your-domain.com"
echo "   - If using HTTPS, update frontend ports in compose to 8080:80"
echo "     and add an nginx reverse proxy block for SSL termination."
echo "========================================="
