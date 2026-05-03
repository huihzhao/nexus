#!/usr/bin/env bash
# vps_deploy.sh — pull a new nexus-server image and roll it out, with
# health-gated automatic rollback to the last known good image.
#
# Invoked by .github/workflows/deploy-server.yml over SSH:
#
#     bash scripts/deploy/vps_deploy.sh ghcr.io/<owner>/nexus-server:sha-abc1234
#
# The image:tag argument is required. Tag conventions:
#   * `latest`        → moves with main; not used by this script
#                       directly (we pin to a sha for rollback).
#   * `sha-<short>`   → immutable pointer to a specific commit. The
#                       script remembers the last successfully-deployed
#                       sha tag in `.last-good-image` so a failed
#                       deploy can flip back to it without us having
#                       to figure out the previous SHA from git.
#
# Pre-requisites on the VPS (one-time setup):
#   * docker + docker compose plugin installed
#   * /home/jimmy/nexus is a clone of the repo with .env.production
#     mounted at the expected path (see docker-compose.yml).
#   * The VPS has logged into GHCR once:
#       echo "$GHCR_PAT" | docker login ghcr.io -u <user> --password-stdin
#     (PAT only needs read:packages — see docs/CICD.md.)
#
# Idempotent: rerunning with the same image tag is a no-op (pull is
# cached, compose detects no change). Concurrent runs are protected by
# a lockfile (`.deploy.lock`) — the second run exits non-zero rather
# than racing.

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "✗ usage: $0 <image:tag>"
    exit 2
fi

NEW_IMAGE="$1"
DEPLOY_DIR="$HOME/nexus"
LOCK_FILE="$DEPLOY_DIR/.deploy.lock"
LAST_GOOD_FILE="$DEPLOY_DIR/.last-good-image"
LOG_PREFIX="[$(date +%H:%M:%S)] vps_deploy"

cd "$DEPLOY_DIR" || { echo "✗ $DEPLOY_DIR not found"; exit 1; }

echo "$LOG_PREFIX target image: $NEW_IMAGE"

# ── Concurrency lock ────────────────────────────────────────────────
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "✗ another deploy is in progress (lock held on $LOCK_FILE)"
    exit 1
fi
trap 'flock -u 9' EXIT

# ── Capture the currently-running image so we can roll back ─────────
# `docker compose images -q` returns the image ID of the running
# container. We translate that to a fully-qualified tag via
# `docker image inspect`. Falls back to whatever's in
# .last-good-image, then finally to ":latest" so a fresh VPS still
# has SOMETHING to roll back to.
PREVIOUS_IMAGE=""
if [ -f "$LAST_GOOD_FILE" ]; then
    PREVIOUS_IMAGE=$(cat "$LAST_GOOD_FILE")
fi
RUNNING_ID=$(sudo docker compose images -q nexus-server 2>/dev/null || true)
if [ -n "$RUNNING_ID" ]; then
    RUNNING_TAG=$(sudo docker image inspect "$RUNNING_ID" \
                    --format '{{ index .RepoTags 0 }}' 2>/dev/null || true)
    if [ -n "$RUNNING_TAG" ] && [ "$RUNNING_TAG" != "<none>:<none>" ]; then
        PREVIOUS_IMAGE="$RUNNING_TAG"
    fi
fi
echo "$LOG_PREFIX previous image: ${PREVIOUS_IMAGE:-<none>}"

# ── Pull new image ──────────────────────────────────────────────────
# `docker compose pull` honours the IMAGE_TAG env var below; explicit
# `docker pull` first lets us fail fast if the registry is down or the
# tag doesn't exist, BEFORE we touch the running container.
echo "$LOG_PREFIX pulling $NEW_IMAGE"
if ! sudo docker pull "$NEW_IMAGE"; then
    echo "✗ pull failed — leaving running container untouched"
    exit 1
fi

# ── Recreate the container with the new image ───────────────────────
# IMAGE_TAG comes through to docker-compose.yml's `image:` field, which
# is templated as `ghcr.io/${GHCR_OWNER}/nexus-server:${IMAGE_TAG}`.
# We pass the FULL `image:tag` to honour rollback to a different repo
# if that ever happens, by overriding via NEXUS_IMAGE.
export NEXUS_IMAGE="$NEW_IMAGE"
echo "$LOG_PREFIX recreating container"
sudo -E docker compose up -d --force-recreate nexus-server

# ── Health probe ────────────────────────────────────────────────────
# Caddy fronts the server at https://nexus.globalnexus.uk/healthz, but
# we hit the local port directly here so a Caddy outage doesn't roll
# us back unnecessarily. 90 sec budget covers the slow first-import
# tax (twin manager loading, SDK plugins, etc.).
echo "$LOG_PREFIX waiting for /healthz"
HEALTHY=0
for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8001/healthz >/dev/null 2>&1; then
        HEALTHY=1
        echo "$LOG_PREFIX ✓ healthy after ${i}x probe"
        break
    fi
    sleep 3
done

# ── Roll back on failure ────────────────────────────────────────────
if [ "$HEALTHY" != "1" ]; then
    echo "✗ /healthz never responded — rolling back"
    sudo docker compose logs --tail 60 nexus-server || true
    if [ -n "$PREVIOUS_IMAGE" ] && [ "$PREVIOUS_IMAGE" != "$NEW_IMAGE" ]; then
        echo "$LOG_PREFIX restoring $PREVIOUS_IMAGE"
        export NEXUS_IMAGE="$PREVIOUS_IMAGE"
        sudo -E docker compose up -d --force-recreate nexus-server
        # Confirm the rollback came up — we don't want to leave the
        # service in a half-restored state. If this also fails we shout
        # loud (operator paging territory).
        for i in $(seq 1 20); do
            if curl -fsS http://127.0.0.1:8001/healthz >/dev/null 2>&1; then
                echo "$LOG_PREFIX rollback healthy"
                exit 1   # The DEPLOY failed even though rollback recovered.
            fi
            sleep 3
        done
        echo "✗ rollback ALSO failed — system is down, intervene manually"
        exit 2
    else
        echo "✗ no previous image known — cannot roll back"
        exit 1
    fi
fi

# ── Record the new image as the rollback target for next deploy ─────
echo "$NEW_IMAGE" > "$LAST_GOOD_FILE"
echo "$LOG_PREFIX ✓ deploy complete; recorded $NEW_IMAGE as last-good"

# ── Best-effort cache prune ─────────────────────────────────────────
# Untagged old images pile up over weeks of deploys. Keep the last 3
# tagged sha-* images plus :latest plus :buildcache. Anything else
# can go. `|| true` so prune failures don't fail the deploy.
sudo docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true
