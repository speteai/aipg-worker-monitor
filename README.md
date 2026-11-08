# aipg-worker-monitor

Know the instant your [AI Power Grid](https://aipowergrid.io) worker drops off the
grid — and keep an eye on what it earns. One file, stdlib only, Python 3.8+, no
dependencies. Sends push notifications through [ntfy.sh](https://ntfy.sh) (free,
no account).

> Unofficial, community-built. Not affiliated with AI Power Grid.

## What it watches

It polls the grid's own account endpoints with your API key:

- **`/v1/account/workers`** — each worker's `online` / `maintenance` state,
  `den_earned`, `jobs_completed`, `last_seen`.
- **`/v1/account/credits`** — your remaining spendable balance.

and sends a push when a worker:

| Event | Priority |
|---|---|
| goes **offline** (was online, now not) | high ⚠ |
| enters **maintenance** | default 🔧 |
| comes **back online** | default ✓ |
| is **stale** (online but `last_seen` too old) | high ⚠ — *opt-in, off by default* |

It alerts **only on a real state change**, never on first sight, and never when a
fetch fails (that's treated as "unknown", not "down") — so you don't get the
false-positive 3 a.m. pings that naive monitors produce.

## Setup

```bash
export AIPG_API_KEY=grid_your_key

# Pick a LONG, RANDOM ntfy topic. The topic name is the only credential —
# anyone who knows it can read your alerts.
export AIPG_NTFY_TOPIC=aipg-$(python3 -c "import secrets;print(secrets.token_hex(10))")
echo "Subscribe to this topic in the ntfy app: $AIPG_NTFY_TOPIC"
```

Install the **ntfy** app (Android / iOS / web at ntfy.sh) and subscribe to that
topic. Then:

```bash
python3 worker-monitor.py --once      # one check (for cron / Task Scheduler)
python3 worker-monitor.py --watch 300 # or loop every 5 minutes
```

Without a topic it runs in print-only mode (status to stdout, no pushes) — handy
for a first look.

### Run it every 5 minutes

**Linux/macOS (cron):**
```cron
*/5 * * * * AIPG_API_KEY=grid_... AIPG_NTFY_TOPIC=aipg-xxxx /usr/bin/python3 /path/worker-monitor.py --once >> ~/.aipg-worker-monitor/log 2>&1
```

**Windows (Task Scheduler)** — your worker likely runs here:
- Program: `python`
- Arguments: `C:\path\worker-monitor.py --once`
- Trigger: repeat every 5 minutes
- Set `AIPG_API_KEY` and `AIPG_NTFY_TOPIC` as user environment variables first.

Or just leave `--watch 300` running in a terminal / `pythonw` background task.

## Example output

```
[21:05:12] workers: 1  online: 1  den_earned: 15.49  jobs: 6
        credits: spendable=$4.961  paid=$4.961
        • 6800-6822: ONLINE  den=15.49  jobs=6 last_seen 2m ago
```

## Notes

- **`last_seen` tracks the last job, not a heartbeat.** An idle but connected
  worker can show a large `last_seen` while genuinely online, so the stale check
  is **off by default** — the grid's `online` flag is the reliable signal. Turn
  the stale check on with `--stale-minutes 30` if you want it anyway.
- State is kept in `~/.aipg-worker-monitor/state.json`.
- Your API key is read from the environment and sent only to `api.aipowergrid.io`.
  Nothing is written anywhere else.

## License

MIT — see [LICENSE](LICENSE).
