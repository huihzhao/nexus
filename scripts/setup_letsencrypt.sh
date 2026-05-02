#!/usr/bin/env bash
# setup_letsencrypt.sh — provision a real Let's Encrypt cert for the
# Nexus server, no Caddy / nginx / docker required.
#
# When to use this
# ================
# You have a public hostname (real domain OR <ip-with-dashes>.nip.io,
# both work for ACME) and you want a real cert that browsers trust
# without any "Not Secure" warning. uvicorn binds 443 directly with
# certbot-issued PEM files.
#
# Why not Caddy
# =============
# Caddy + the Docker setup in this repo is the recommended path. Use
# this script only if:
#   * You already started the server with `uv run nexus-server` and
#     don't want to migrate to Docker.
#   * You don't want a reverse proxy in front.
#
# Trade-off: certbot --standalone needs port 80 free during issuance,
# and the auto-renew cron has to STOP the server, run certbot, and
# RESTART it — Caddy renews in-process with zero downtime, certbot
# standalone has a few-seconds gap.
#
# Usage
# =====
#   sudo ./scripts/setup_letsencrypt.sh nexus.your-domain.com
#   sudo ./scripts/setup_letsencrypt.sh 165-227-135-198.nip.io
#
# Prereqs
# =======
#   * Port 80 reachable from the public internet (firewall + any
#     cloud-provider ingress rule)
#   * sudo privileges to bind 80 + write to /etc/letsencrypt
#   * certbot installed:
#       Debian/Ubuntu: apt install certbot
#       macOS:         brew install certbot
#
# After this runs, start the server with:
#   sudo SSL_CERTFILE=/etc/letsencrypt/live/<host>/fullchain.pem \
#        SSL_KEYFILE=/etc/letsencrypt/live/<host>/privkey.pem \
#        uv run nexus-server --port 443
# (sudo because port 443 is privileged. Or run on 8443 without sudo
# and use a port-forward.)

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <hostname>"
    echo "Example: $0 nexus.example.com"
    echo "Example: $0 1-2-3-4.nip.io"
    exit 1
fi
HOSTNAME="$1"

if ! command -v certbot >/dev/null 2>&1; then
    echo "✗ certbot not installed. Install with:"
    echo "    Debian/Ubuntu: sudo apt install certbot"
    echo "    macOS:         brew install certbot"
    exit 1
fi

if [ "$EUID" -ne 0 ]; then
    echo "✗ Run as root (sudo) — certbot needs to bind port 80 + write /etc/letsencrypt."
    exit 1
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Provisioning Let's Encrypt cert for: $HOSTNAME"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Make sure port 80 is open + reachable from the internet right now."
echo "If anything else is bound to :80 (nginx, Caddy, the server itself)"
echo "stop it first — certbot's standalone mode owns :80 during issuance."
echo ""
read -p "Press Enter to continue, Ctrl-C to abort... "

certbot certonly \
    --standalone \
    --non-interactive \
    --agree-tos \
    --register-unsafely-without-email \
    --domain "$HOSTNAME" \
    --preferred-challenges http

CERT="/etc/letsencrypt/live/$HOSTNAME/fullchain.pem"
KEY="/etc/letsencrypt/live/$HOSTNAME/privkey.pem"

if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
    echo "✗ certbot finished but expected cert files are missing:"
    echo "  $CERT"
    echo "  $KEY"
    exit 1
fi

echo ""
echo "✓ Cert provisioned at $CERT"
echo ""
echo "Auto-renewal: certbot installs a systemd timer (or cron job on"
echo "older systems) that runs 'certbot renew' twice daily. To make"
echo "the server pick up the renewed cert, add a deploy-hook:"
echo ""
echo "  sudo tee /etc/letsencrypt/renewal-hooks/deploy/nexus.sh <<'HOOK'"
echo "  #!/bin/sh"
echo "  systemctl restart nexus-server   # or whatever your service is"
echo "  HOOK"
echo "  sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/nexus.sh"
echo ""
echo "Start the server with:"
echo ""
echo "  sudo SSL_CERTFILE=$CERT \\"
echo "       SSL_KEYFILE=$KEY \\"
echo "       uv run nexus-server --port 443"
echo ""
echo "Then point the desktop at  https://$HOSTNAME"
