#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   sudo PROJECT_GIT_URL="https://github.com/<you>/<repo>.git" PROJECT_DIR="/opt/burgerbot" /bin/bash scripts/ec2-bootstrap.sh
#
# Optional environment variables:
#   PROJECT_GIT_URL  (required) git clone URL
#   PROJECT_DIR      default: /opt/burgerbot
#   SERVICE_NAME     default: burgerbot

PROJECT_GIT_URL="${PROJECT_GIT_URL:-}"
PROJECT_DIR="${PROJECT_DIR:-/opt/burgerbot}"
SERVICE_NAME="${SERVICE_NAME:-burgerbot}"

if [[ -z "${PROJECT_GIT_URL}" ]]; then
  echo "ERROR: PROJECT_GIT_URL is required"
  exit 1
fi

echo "[1/7] Installing docker and git..."
apt-get update -y
apt-get install -y docker.io docker-compose-plugin git

echo "[2/7] Enabling docker..."
systemctl enable docker
systemctl start docker

echo "[3/7] Preparing app directory..."
mkdir -p "${PROJECT_DIR}"
if [[ ! -d "${PROJECT_DIR}/.git" ]]; then
  git clone "${PROJECT_GIT_URL}" "${PROJECT_DIR}"
else
  git -C "${PROJECT_DIR}" pull --ff-only
fi

echo "[4/7] Checking required env file..."
if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
  echo "ERROR: ${PROJECT_DIR}/.env not found. Create it before running this script."
  exit 1
fi

echo "[5/7] Building and starting containers..."
docker compose -f "${PROJECT_DIR}/docker-compose.yml" --env-file "${PROJECT_DIR}/.env" up -d --build

echo "[6/7] Writing systemd service for auto-restart on reboot..."
cat >/etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=BurgerBot Trading Backend
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${PROJECT_DIR}
ExecStart=/usr/bin/docker compose --env-file ${PROJECT_DIR}/.env up -d
ExecStop=/usr/bin/docker compose --env-file ${PROJECT_DIR}/.env down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

echo "[7/7] Enabling systemd service..."
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl start ${SERVICE_NAME}

echo "Done."
echo "Health endpoint: http://<ec2-public-ip>:8000/health"
