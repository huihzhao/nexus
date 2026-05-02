#!/usr/bin/env bash
# run_prod_locally.sh — bring up the full production stack on this Mac.
#
# Why
# ===
# Same Dockerfile, same docker-compose.yml, same Caddyfile that runs in
# production, but pointed at `nexus.localhost` instead of a public
# nip.io subdomain. Caddy auto-uses its internal CA for any
# `.localhost` host (it skips ACME entirely), so you get a real TLS
# handshake against a real reverse proxy without needing a public IP /
# Let's Encrypt / nip.io.
#
# What this is good for
# =====================
#   * Verifying the Docker image actually builds + boots
#   * Verifying the volume persistence (`/data` on a named volume)
#   * Verifying Caddy reverse-proxy config (X-Forwarded-* headers,
#     long-poll SSE timeouts)
#   * Verifying WebAuthn passkeys work end-to-end through Caddy
#   * Verifying the desktop's Welcome wizard accepts the URL
#
# Limits
# ======
# Caddy uses its internal CA, so the desktop / browsers don't trust it
# by default. We trust the CA on the host once (via `security
# add-trusted-cert`); the desktop's HttpClient then sees a system-CA
# signed cert and is happy without any "Trust self-signed" checkbox.
#
# To go all the way to "real Let's Encrypt cert" you need a public
# domain — at that point you're doing a real deploy, not a local sim.
#
# Usage
# =====
#   ./scripts/run_prod_locally.sh         # start
#   ./scripts/run_prod_locally.sh stop    # stop + clean up
#
# After running, the desktop talks to https://nexus.localhost.

set -euo pipefail

cd "$(dirname "$0")/.."

ACTION="${1:-start}"
HOSTNAME_LOCAL="nexus.localhost"
ENV_FILE=".env.production"
ENV_BAK=".env.production.bak"

if [ "$ACTION" = "stop" ]; then
    echo "→ docker compose down"
    docker compose down
    if [ -f "$ENV_BAK" ]; then
        mv "$ENV_BAK" "$ENV_FILE"
        echo "→ restored original $ENV_FILE"
    fi
    echo "✓ stopped."
    exit 0
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Local production simulation"
echo "  hostname: https://$HOSTNAME_LOCAL"
echo "════════════════════════════════════════════════════════════════"
echo ""

# ── Sanity: docker available ─────────────────────────────────────────
command -v docker >/dev/null || { echo "✗ docker not installed"; exit 1; }
docker compose version >/dev/null || { echo "✗ 'docker compose' missing"; exit 1; }

# ── Stash existing .env.production, write a localhost-flavoured one ──
if [ -f "$ENV_FILE" ] && [ ! -f "$ENV_BAK" ]; then
    cp "$ENV_FILE" "$ENV_BAK"
    echo "→ backed up existing $ENV_FILE → $ENV_BAK"
fi

if [ ! -f ".env.production.example" ]; then
    echo "✗ .env.production.example missing — did you delete the deploy artifacts?"
    exit 1
fi

cp .env.production.example "$ENV_FILE"

# Patch the env file for local sim:
#   * HOSTNAME → nexus.localhost (Caddy uses internal CA for .localhost)
#   * WEBAUTHN_RP_ID / ORIGIN / CORS → nexus.localhost
#   * SERVER_SECRET → random (don't reuse prod secret)
#   * GEMINI_API_KEY → kept from .env.production.bak if it had one
set_env() {
    local key="$1" val="$2"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        # Use a marker char that's unlikely in values.
        sed -i.tmp "s|^${key}=.*|${key}=${val}|" "$ENV_FILE" && rm -f "$ENV_FILE.tmp"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}
set_env "HOSTNAME"           "$HOSTNAME_LOCAL"
set_env "WEBAUTHN_RP_ID"     "$HOSTNAME_LOCAL"
set_env "WEBAUTHN_ORIGIN"    "https://$HOSTNAME_LOCAL"
set_env "CORS_ALLOW_ORIGINS" "https://$HOSTNAME_LOCAL"
set_env "SERVER_SECRET"      "$(openssl rand -hex 32)"

# Keep GEMINI_API_KEY from backup if present
if [ -f "$ENV_BAK" ]; then
    GEMINI=$(grep -E '^GEMINI_API_KEY=' "$ENV_BAK" | head -1 | cut -d= -f2- || true)
    if [ -n "$GEMINI" ]; then
        set_env "GEMINI_API_KEY" "$GEMINI"
        echo "→ carried GEMINI_API_KEY over from $ENV_BAK"
    fi
fi
echo "→ wrote $ENV_FILE for local sim"

# ── Bring up the stack ───────────────────────────────────────────────
echo ""
echo "→ docker compose up --build -d"
docker compose up --build -d

echo ""
echo "→ waiting 12 s for Caddy to provision its internal CA + start nexus-server"
sleep 12

# ── Pull Caddy's internal CA root cert out of the volume + trust it ──
# Caddy stores its internal CA at /data/caddy/pki/authorities/local/.
# We extract root.crt and add it to the macOS system keychain so the
# desktop's HttpClient (which uses system trust store) accepts certs
# Caddy issues for nexus.localhost.
CA_FILE="caddy-local-ca.crt"
echo ""
echo "→ extracting Caddy internal CA"
docker compose exec -T caddy \
    cat /data/caddy/pki/authorities/local/root.crt > "$CA_FILE" 2>/dev/null \
    || { echo "✗ couldn't read CA from caddy container — is it healthy?"; \
         docker compose logs --tail 30 caddy; exit 1; }

if [ ! -s "$CA_FILE" ]; then
    echo "✗ CA file is empty. Caddy might not have generated it yet."
    echo "  Run: docker compose logs caddy"
    exit 1
fi

# macOS only path. Linux users have it harder (per-browser trust stores).
if [[ "$OSTYPE" == darwin* ]]; then
    echo ""
    echo "→ trusting Caddy CA in macOS System keychain (sudo required)"
    if sudo security add-trusted-cert -d -r trustRoot \
        -k /Library/Keychains/System.keychain "$CA_FILE"; then
        echo "✓ CA trusted"
    else
        echo "⚠ trust install failed — cert is at ./$CA_FILE,"
        echo "  drag it into Keychain Access → System → set 'Always Trust'"
    fi
else
    echo "⚠ non-macOS detected — manually trust ./$CA_FILE in your browser"
fi

# ── Show the user what to do next ────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Stack is up"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "  Server URL:      https://$HOSTNAME_LOCAL"
echo "  Health check:    curl https://$HOSTNAME_LOCAL/healthz"
echo ""
echo "  Browser:         open https://$HOSTNAME_LOCAL/auth/passkey-page"
echo "                   → green lock, no warning"
echo ""
echo "  Desktop:         open the Welcome wizard, paste"
echo "                   https://$HOSTNAME_LOCAL"
echo "                   leave 'Trust self-signed' UNCHECKED — Caddy's"
echo "                   CA is trusted system-wide now."
echo ""
echo "  Logs:            docker compose logs -f nexus-server"
echo "                   docker compose logs -f caddy"
echo ""
echo "  Stop:            ./scripts/run_prod_locally.sh stop"
echo "                   (also restores your real .env.production)"
echo ""
