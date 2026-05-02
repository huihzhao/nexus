#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
watch_brain.py — Live monitor for the desktop's Brain Dashboard.

Polls /agent/chain_status + /agent/learning_summary + /agent/evolution/pressure
every 2 s (same cadence as the desktop) and prints a compact diff so you can
verify that section 1–4 in the Cognition column are actually receiving fresh
data.

Usage:
  # one-shot
  python3 scripts/watch_brain.py --token "$JWT"

  # live (Ctrl-C to quit)
  python3 scripts/watch_brain.py --token "$JWT" --watch

  # JWT file
  python3 scripts/watch_brain.py --token-file ~/.nexus-token --watch

Server URL defaults to http://localhost:8001; override with --url.

What you'll see:

  [20:30:12] Glance facts=34 (+0)  skills=31 (+0)  knowledge=0  persona=v—  episodes=0
             chain  daemon=ok  greenfield=ok  bsc=off    wal=0
             timeline 7d  facts=[0,0,0,0,0,2,32]  skills=[…]  knowledge=[…]
             dataflow KnowledgeCompiler 0/10 to compile · PersonaEvolver 0d to next
             just_learned 12 items (top: SKILL "The AI successfully…")

When you chat with the agent, expect:
  * facts +1..+3 within 2-5 s after agent reply
  * skills +0..+1 within 2-5 s
  * just_learned newest item changes
  * timeline last bucket bumps
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error


def fetch(base: str, path: str, token: str) -> dict | None:
    """GET a server endpoint with the JWT. Return parsed JSON or None."""
    req = urllib.request.Request(
        base.rstrip("/") + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"[error] {path} → {e.code} {e.reason}\n")
        return None
    except Exception as e:
        sys.stderr.write(f"[error] {path} → {e}\n")
        return None


def fmt_glance(learn: dict, chain: dict) -> str:
    """Section 1 — Brain at a Glance (5 cards)."""
    if not learn or not chain:
        return "  (no data)"
    timeline = learn.get("timeline") or []
    today = timeline[-1] if timeline else {}
    week = {
        k: sum(d.get(k, 0) for d in timeline)
        for k in ("facts", "skills", "knowledge", "persona", "episodes")
    }
    by_ns = {n["namespace"]: n for n in chain.get("namespaces", [])}
    persona_v = by_ns.get("persona", {}).get("version") or "—"
    return (
        f"  Glance "
        f"facts={week['facts']} (+{today.get('facts', 0)} today)  "
        f"skills={week['skills']} (+{today.get('skills', 0)} today)  "
        f"knowledge={week['knowledge']}  "
        f"persona=v{persona_v}  "
        f"episodes={week['episodes']}"
    )


def fmt_chain(chain: dict) -> str:
    """Chain Health card + per-namespace dot status."""
    if not chain:
        return "  (no data)"
    h = chain.get("health", {})
    flag = lambda v: "ok" if v else "off"
    nss = chain.get("namespaces", [])
    dots_per_ns = "  ".join(
        f"{n['namespace'][:4]}={n.get('status', '?')}" for n in nss
    )
    return (
        f"  chain  "
        f"daemon={flag(h.get('daemon_alive'))}  "
        f"greenfield={flag(h.get('greenfield_ready'))}  "
        f"bsc={flag(h.get('bsc_ready'))}  "
        f"wal={h.get('wal_queue_size', 0)}\n"
        f"         {dots_per_ns}"
    )


def fmt_timeline(learn: dict) -> str:
    """Section 2 — 7-day series, one line per namespace."""
    timeline = learn.get("timeline") or []
    if not timeline:
        return "  timeline 7d  (empty)"
    series = lambda key: [d.get(key, 0) for d in timeline]
    return (
        f"  timeline 7d  "
        f"facts={series('facts')}  "
        f"skills={series('skills')}  "
        f"knowledge={series('knowledge')}"
    )


def fmt_dataflow(learn: dict) -> str:
    """Section 3 — DAG node statuses (Knowledge / Persona pressure)."""
    flow = learn.get("data_flow") or []
    if not flow:
        return "  dataflow (empty)"
    parts = []
    for s in flow:
        evo = s.get("evolver", "?")
        if evo not in ("KnowledgeCompiler", "PersonaEvolver"):
            continue
        acc = s.get("accumulator", 0)
        thr = s.get("threshold", 0)
        unit = s.get("unit", "")
        if thr and thr != float("inf"):
            parts.append(f"{evo} {int(acc)}/{int(thr)} {unit}")
        else:
            parts.append(f"{evo} {int(acc)} {unit}")
    return f"  dataflow {' · '.join(parts) if parts else '(no pressure data)'}"


def fmt_just_learned(learn: dict) -> str:
    """Section 4 — Just Learned feed (top item only)."""
    items = learn.get("just_learned") or []
    if not items:
        return "  just_learned (empty)"
    top = items[0]
    kind = top.get("kind", "?").upper()
    content = (top.get("content") or "")[:60]
    return f"  just_learned {len(items)} items (top: {kind} \"{content}…\")"


def fmt_pressure(press: dict) -> str:
    """Pressure dashboard top-level summary."""
    if not press:
        return "  pressure (no data)"
    evolvers = press.get("evolvers", [])
    verdicts = press.get("recent_verdicts", [])
    by_status = {}
    for e in evolvers:
        by_status[e.get("status", "?")] = by_status.get(e.get("status", "?"), 0) + 1
    parts = " ".join(f"{s}:{n}" for s, n in by_status.items())
    return (
        f"  pressure "
        f"{len(evolvers)} evolvers ({parts or 'none'})  "
        f"recent_verdicts={len(verdicts)}"
    )


def snapshot(base: str, token: str) -> tuple[dict, dict, dict]:
    chain = fetch(base, "/api/v1/agent/chain_status", token) or {}
    learn = fetch(base, "/api/v1/agent/learning_summary?window=7d", token) or {}
    press = fetch(base, "/api/v1/agent/evolution/pressure", token) or {}
    return chain, learn, press


def print_snapshot(chain: dict, learn: dict, press: dict) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}]")
    print(fmt_glance(learn, chain))
    print(fmt_chain(chain))
    print(fmt_timeline(learn))
    print(fmt_dataflow(learn))
    print(fmt_just_learned(learn))
    print(fmt_pressure(press))
    print()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://localhost:8001",
                   help="Server base URL (default: http://localhost:8001)")
    p.add_argument("--token", help="JWT bearer token")
    p.add_argument("--token-file", help="Path to a file containing the JWT")
    p.add_argument("--watch", action="store_true",
                   help="Poll every 2s instead of one-shot")
    p.add_argument("--interval", type=float, default=2.0,
                   help="Poll interval in seconds (default: 2.0)")
    args = p.parse_args()

    token = args.token
    if not token and args.token_file:
        token = open(os.path.expanduser(args.token_file)).read().strip()
    if not token:
        token = os.environ.get("NEXUS_TOKEN", "")
    if not token:
        sys.stderr.write(
            "error: provide --token, --token-file, or NEXUS_TOKEN env var\n"
            "tip:   on macOS the desktop stores it in ~/Library/Application "
            "Support/RuneDesktop/auth.json — grep for 'token'\n",
        )
        return 2

    if not args.watch:
        chain, learn, press = snapshot(args.url, token)
        print_snapshot(chain, learn, press)
        return 0

    print(f"watching {args.url} every {args.interval}s. Ctrl-C to quit.")
    try:
        while True:
            chain, learn, press = snapshot(args.url, token)
            print_snapshot(chain, learn, press)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
