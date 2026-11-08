#!/usr/bin/env python3
"""
aipg-worker-monitor — know the instant your AI Power Grid worker drops off the
grid, and watch what it earns.

It polls the grid's own account endpoints:
  • /v1/account/workers  — per-worker online / maintenance / den_earned / jobs / last_seen
  • /v1/account/credits  — your remaining credit balance

and sends a push notification (via ntfy.sh, free, no account) when a worker:
  • goes OFFLINE (was online, now not)                     [alert]
  • enters MAINTENANCE                                      [alert]
  • comes back ONLINE / out of maintenance                 [recovery]
  • hasn't been seen for --stale-minutes                   [alert]

Run it once from cron/Task Scheduler (--once) or as a loop (--watch 300).
Stdlib only (Python 3.8+); no pip install. Needs only your grid API key.

Setup:
  export AIPG_API_KEY=grid_your_key
  export AIPG_NTFY_TOPIC=aipg-myworker-$(openssl rand -hex 8)   # keep this secret
  python3 worker-monitor.py --once           # cron every 5 min
  python3 worker-monitor.py --watch 300       # or run as a loop

Subscribe to the same topic in the ntfy app (Android/iOS/web) to get pushes.
The topic name IS the credential — anyone who knows it can read your alerts, so
use a long random one.
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = "https://api.aipowergrid.io/v1"
UA = "aipg-worker-monitor/1.0"
STATE_DIR = os.path.expanduser("~/.aipg-worker-monitor")
STATE_FILE = os.path.join(STATE_DIR, "state.json")


def api(path, api_key):
    req = urllib.request.Request(BASE + path)
    req.add_header("User-Agent", UA)
    req.add_header("Authorization", "Bearer " + api_key)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def ntfy(topic, server, title, message, priority="default", tags=""):
    if not topic:
        return
    url = server.rstrip("/") + "/" + topic
    req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
    req.add_header("User-Agent", UA)
    req.add_header("Title", title.encode("ascii", "ignore").decode())  # ntfy headers are latin-1; keep ASCII, icons come from Tags
    req.add_header("Priority", priority)
    if tags:
        req.add_header("Tags", tags)
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"  ! ntfy push failed: {e}", file=sys.stderr)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)


def age_minutes(iso):
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - t).total_seconds() / 60
    except Exception:
        return None


def credits_line(api_key):
    try:
        c = api("/account/credits", api_key)
    except Exception:
        return None
    parts = []
    total = c.get("total_spendable_usd")
    if total is not None:
        parts.append(f"spendable=${total:.3f}")
    # paid uses balance_usd; promotional/free use remaining_usd
    paid = (c.get("paid") or {}).get("balance_usd")
    if paid:
        parts.append(f"paid=${paid:.3f}")
    for k in ("promotional", "free"):
        usd = (c.get(k) or {}).get("remaining_usd")
        if usd:
            parts.append(f"{k}=${usd:.3f}")
    return "  ".join(parts) if parts else None


def check(api_key, topic, server, stale_min, quiet):
    try:
        data = api("/account/workers", api_key)
    except urllib.error.HTTPError as e:
        print(f"[{datetime.now():%H:%M:%S}] API error {e.code} — treating as UNKNOWN, no alert")
        return
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] fetch failed ({e}) — treating as UNKNOWN, no alert")
        return

    state = load_state()
    workers = data.get("workers", [])
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] workers: {data.get('count',0)}  online: {data.get('online',0)}  "
          f"den_earned: {data.get('den_earned',0)}  jobs: {data.get('jobs_completed',0)}")
    cl = credits_line(api_key)
    if cl:
        print(f"        credits: {cl}")

    for w in workers:
        name = w.get("name", "?")
        online = bool(w.get("online"))
        maint = bool(w.get("maintenance"))
        den = w.get("den_earned", 0)
        seen = w.get("last_seen", "")
        stale = age_minutes(seen)
        flag = "ONLINE" if online else "OFFLINE"
        if maint:
            flag += " (maintenance)"
        extra = f" last_seen {stale:.0f}m ago" if stale is not None else ""
        print(f"        • {name}: {flag}  den={den}  jobs={w.get('jobs_completed',0)}{extra}")

        prev = state.get(name, {})
        prev_online = prev.get("online")
        prev_maint = prev.get("maintenance")

        # transitions (only alert on a real change, never on first sight / unknown)
        if prev_online is True and not online:
            ntfy(topic, server, f"Worker offline: {name}",
                 f"{name} dropped off the grid (last seen {stale:.0f}m ago)." if stale is not None
                 else f"{name} dropped off the grid.", "high", "warning")
        elif prev_online is False and online:
            ntfy(topic, server, f"Worker back online: {name}",
                 f"{name} is serving the grid again. den={den}", "default", "white_check_mark")
        if prev_maint is False and maint:
            ntfy(topic, server, f"Worker in maintenance: {name}",
                 f"{name} entered maintenance mode.", "default", "wrench")

        # stale heartbeat while nominally online — OPT-IN only (stale_min > 0).
        # last_seen appears to track the last *job*, not a heartbeat, so an idle
        # but connected worker can show a large last_seen while genuinely online.
        # The grid's own `online` flag is the reliable liveness signal; rely on it.
        if stale_min and online and stale is not None and stale >= stale_min and not prev.get("stale_alerted"):
            ntfy(topic, server, f"Worker stale: {name}",
                 f"{name} reports online but hasn't been seen for {stale:.0f} min.", "high", "warning")
            prev["stale_alerted"] = True
        elif stale is not None and stale < stale_min:
            prev["stale_alerted"] = False

        prev.update({"online": online, "maintenance": maint, "den": den, "last_seen": seen})
        state[name] = prev

    save_state(state)
    if quiet:
        return


def main():
    ap = argparse.ArgumentParser(description="Monitor AI Power Grid worker status + earnings, with ntfy alerts.")
    ap.add_argument("--once", action="store_true", help="Check once and exit (for cron / Task Scheduler)")
    ap.add_argument("--watch", type=int, metavar="SECONDS", help="Loop, checking every N seconds")
    ap.add_argument("--stale-minutes", type=float, default=0, help="Opt-in: alert if an online worker's last_seen exceeds this many minutes. Default 0 = off, because last_seen tracks the last job, not a heartbeat (rely on the online flag instead).")
    ap.add_argument("--ntfy-topic", default=os.environ.get("AIPG_NTFY_TOPIC", ""), help="ntfy topic (or set AIPG_NTFY_TOPIC)")
    ap.add_argument("--ntfy-server", default=os.environ.get("AIPG_NTFY_SERVER", "https://ntfy.sh"))
    ap.add_argument("--quiet", action="store_true", help="Less stdout (alerts still fire)")
    args = ap.parse_args()

    api_key = os.environ.get("AIPG_API_KEY")
    if not api_key:
        print("ERROR: set AIPG_API_KEY to your grid_... key.", file=sys.stderr)
        sys.exit(2)
    if not args.ntfy_topic:
        print("Note: no ntfy topic set (AIPG_NTFY_TOPIC) — running in print-only mode, no pushes.\n")

    if args.watch:
        print(f"Watching every {args.watch}s. Ctrl-C to stop.")
        try:
            while True:
                check(api_key, args.ntfy_topic, args.ntfy_server, args.stale_minutes, args.quiet)
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nstopped.")
    else:
        check(api_key, args.ntfy_topic, args.ntfy_server, args.stale_minutes, args.quiet)


if __name__ == "__main__":
    main()
