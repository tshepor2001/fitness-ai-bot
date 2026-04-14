#!/usr/bin/env bash
# Deploy fitness-ai-bot to a Linode Nanode 1GB ($5/mo).
# Prerequisites: linode-cli installed & configured, or use the Linode dashboard.
set -euo pipefail

LABEL="fitness-ai-bot"
REGION="eu-central"       # Frankfurt — change to your nearest region
IMAGE="linode/ubuntu24.04"
TYPE="g6-nanode-1"        # Nanode 1GB — $5/mo

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

IP=$(linode-cli linodes view "$LINODE_ID" --json | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['ipv4'][0])")
echo "==> Public IP: $IP"

echo "==> Copying project files…"
rsync -avz --exclude '.env' --exclude '__pycache__' \
  -e "ssh -o StrictHostKeyChecking=no" \
  . "root@${IP}:/opt/fitness-ai-bot/"

echo "==> Installing Docker & starting bot…"
ssh -o StrictHostKeyChecking=no "root@${IP}" << 'REMOTE'
  apt-get update && apt-get install -y docker.io
  systemctl enable --now docker
  cd /opt/fitness-ai-bot
  docker build -t fitness-ai-bot .
  mkdir -p /opt/fitness-ai-bot/data
  docker run -d --name fitness-bot --restart unless-stopped \
    --env-file .env \
    -v /opt/fitness-ai-bot/data:/app/data \
    fitness-ai-bot
REMOTE

echo ""
echo "Done! Bot is running on $IP"
echo "Remember to scp your .env file:"
echo "  scp .env root@${IP}:/opt/fitness-ai-bot/.env"
echo "  ssh root@${IP} 'cd /opt/fitness-ai-bot && docker restart fitness-bot'"
