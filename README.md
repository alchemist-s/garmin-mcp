# garmin-mcp

An MCP server that reads your Garmin Connect data — sleep, HRV, training
readiness, activities, body composition — so an assistant can answer questions
about it directly.

## Authentication, and the caveat

Garmin's official Health API is only issued under a commercial partner
agreement, so this uses the same private endpoints the Garmin Connect app does,
via [`garminconnect`](https://github.com/cyberjunky/python-garminconnect). That
works well for your own account and is what every Garmin integration of this
kind does, but it is unsupported by Garmin: endpoints can change without notice,
and aggressive polling can get an account rate-limited.

You log in **once**, interactively. Credentials are exchanged for OAuth tokens
that are cached on disk and last about a year; the server itself never sees a
password and never logs in. That split is deliberate — MFA prompts cannot be
answered over an MCP stdio connection, so a server that tried to log in would
just hang.

## Setup

```sh
cd /Users/al/Dev/garmin-mcp
uv sync
uv run garmin-mcp login     # prompts for email, password, and MFA code if enabled
uv run garmin-mcp status    # confirms the tokens work
```

Tokens land in `~/.garminconnect` (override with `GARMINTOKENS`), written
`0600` in a `0700` directory.

### Wire it into Claude Code

```sh
claude mcp add garmin -- uv --directory /Users/al/Dev/garmin-mcp run garmin-mcp
```

### Wire it into Claude Desktop

In `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uv",
      "args": ["--directory", "/Users/al/Dev/garmin-mcp", "run", "garmin-mcp"]
    }
  }
}
```

If `uv` is not on the launcher's PATH, use its absolute path — `which uv`.

## Tools

Dates accept `YYYY-MM-DD`, `today`, `yesterday`, or `-7d`. Omitting a date means
today; omitting a range means the last seven days.

| Tool | What it returns |
| --- | --- |
| `garmin_whoami` | Account name and unit preferences |
| `garmin_devices` | Registered devices and last sync |
| `garmin_daily_summary` | Steps, calories, floors, stress, body battery for a day |
| `garmin_sleep` | Stages, sleep score, overnight SpO2 and HRV |
| `garmin_heart_rate` | Resting/min/max plus daily average |
| `garmin_hrv` | Overnight HRV against your baseline |
| `garmin_stress` | Time spent in each stress band |
| `garmin_body_battery` | Charge and drain per day over a range |
| `garmin_steps` | Daily totals, or 15-minute buckets via `intraday_date` |
| `garmin_spo2` / `garmin_respiration` | Pulse ox and breathing rate |
| `garmin_intensity_minutes` | Moderate and vigorous minutes |
| `garmin_training_readiness` | Readiness score and its contributing factors |
| `garmin_training_status` | Status, acute/chronic load balance, VO2 max |
| `garmin_vo2max` | VO2 max, fitness age, heat/altitude acclimation |
| `garmin_race_predictions` | Predicted 5K / 10K / half / marathon times |
| `garmin_personal_records` | PRs by activity type |
| `garmin_activities` | Recent activities, newest first |
| `garmin_activities_by_date` | Activities in a date range |
| `garmin_last_activity` | The most recent activity |
| `garmin_activity` | One activity in detail (`raw=true` for chart samples) |
| `garmin_activity_splits` | Per-lap splits |
| `garmin_activity_weather` | Weather during an activity |
| `garmin_weight` | Weigh-ins and body composition |
| `garmin_api_get` | Any Garmin API path, for what the above do not cover |

### Why the responses are trimmed

Garmin's payloads are built for a dashboard: one night of sleep carries
thousands of per-minute samples, and a 20-activity list runs past 100 kB of
mostly-null fields. Every tool returns a compact projection and keeps the raw
payload behind `raw=true`. Anything still over ~60 kB comes back as an explicit
`truncated` marker rather than cut-off JSON — truncated JSON reads as complete
data that happens to end early, and gets summarised as fact.

### Writes

Read-only by default. Setting `GARMIN_MCP_ENABLE_WRITES=1` adds
`garmin_rename_activity`, `garmin_add_weight` and `garmin_add_hydration`.
Nothing deletes, by design — these tools act on a real health record that syncs
back to the watch.

## Troubleshooting

- **"No Garmin tokens…"** — run `garmin-mcp login`.
- **Tokens rejected** — they expire after about a year, and a password change
  invalidates them. `garmin-mcp login --force`.
- **Rate-limited** — Garmin throttles per account. Wait a few minutes; avoid
  looping over long date ranges a day at a time.
- **Today's numbers look wrong** — Garmin only has what the watch last synced to
  your phone.
- **China accounts** — `garmin-mcp login --china`.

## Testing

Four levels, cheapest first.

**1. Offline suite — no account, no network.**

```sh
uv run pytest
```

Runs against a stub shaped like real Garmin payloads. Covers date parsing,
response shaping, every tool's projection logic, and the error paths.

**2. Live smoke test — one command, hits your real account.**

```sh
uv run garmin-mcp login     # once
uv run garmin-mcp check
```

`check` calls every read-only tool through the real MCP dispatch path and
prints one line each:

```
  garmin_sleep                 ok    {"date": "2026-09-20", "sleepTimeSeconds": 27000, …}
  garmin_hrv                   none  (no data for this date)
  garmin_activity_weather      FAIL  Garmin returned 500

23/24 tools responded (1 with no data), 0 failed.
```

`none` is not a failure — it means your device does not record that metric, or
has not synced it for that date. Use `--date 2026-09-19` to test against a day
that has definitely synced; today is often partial.

**3. MCP Inspector — poke individual tools in a browser.**

```sh
uv run mcp dev src/garmin_mcp/server.py:mcp
```

Opens a UI where you can list tools, read their schemas, and call them with
your own arguments. Needs `npx`.

**4. End to end in Claude Code.**

```sh
claude mcp add garmin -- uv --directory /Users/al/Dev/garmin-mcp run garmin-mcp
claude mcp list          # should show garmin as connected
```

Then ask something that needs real data — "how did I sleep last night?", "what
was my longest run this month?", "is my HRV trending down?" — and check the
numbers against the Garmin Connect app.

To test the write tools, add `-e GARMIN_MCP_ENABLE_WRITES=1` to the
`claude mcp add` command.
