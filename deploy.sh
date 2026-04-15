#!/usr/bin/env bash
# Deploy fitness-ai-bot to a Linode instance.
# Prerequisites: linode-cli installed & configured, ssh key available.
# Usage:  ./deploy.sh            — provision new Linode & deploy
#         ./deploy.sh <IP>       — redeploy to existing server
set -euo pipefail

LABEL="fitness-ai-bot"
REGION="eu-central"          # Frankfurt — change to your nearest region
IMAGE="linode/ubuntu24.04"
TYPE="g6-standard-1"         # 2GB RAM, 1 CPU — $12/mo (enough for the API + MCP subprocesses)

if [[ "${1:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  IP="$1"
  echo "==> Redeploying to existing server at $IP"
else
  echo "==> Creating Linode instance…"
  LINODE_ID=$(linode-cli linodes create \
    --label "$LABEL" \
    --region "$REGION" \
    --image "$IMAGE" \
    --type "$TYPE" \
    --root_pass "$(openssl rand -base64 24)" \
    --authorized_keys "$(cat ~/.ssh/id_ed25519.pub 2>/dev/null || cat ~/.ssh/id_rsa.pub)" \
    --json | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])")

  echo "==> Linode $LINODE_ID created. Waiting for boot…"
  sleep 60

  IP=$(linode-cli linodes view "$LINODE_ID" --json \
    | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ipv4'][0])")
  echo "==> Public IP: $IP"
fi

echo "==> Copying project files…"
rsync -avz \
  --exclude '.env' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude '.git' \
  --exclude 'data/' \
  -e "ssh -o StrictHostKeyChecking=no" \
  . "root@${IP}:/opt/fitness-ai-bot/"

echo "==> Installing Docker & deploying…"
ssh -o StrictHostKeyChecking=no "root@${IP}" << 'REMOTE'
  set -euo pipefail

  # Install Docker if needed
  if ! command -v docker &>/dev/null; then
    apt-get update && apt-get install -y docker.io docker-compose-v2
    systemctl enable --now docker
  fi

  cd /opt/fitness-ai-bot

  # Build and start with docker compose
  docker compose down --remove-orphans 2>/dev/null || true
  docker compose up -d --build

  echo ""
  echo "==> Container status:"
  docker compose ps
REMOTE

echo ""
echo "============================================"
echo "  Deployed to http://${IP}:8000"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Copy your .env:  scp .env root@${IP}:/opt/fitness-ai-bot/.env"
echo "  2. Restart:         ssh root@${IP} 'cd /opt/fitness-ai-bot && docker compose up -d'"
echo "  3. Check health:    curl http://${IP}:8000/health"
echo ""
echo "To redeploy after code changes:  ./deploy.sh ${IP}"
