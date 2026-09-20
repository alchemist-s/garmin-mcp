# Zone Two

Your training log, in conversation. An MCP server that reads your own Garmin
Connect data — sleep, HRV, training readiness, activities, body composition —
so Claude can answer questions about it directly.

**[zonetwo.vercel.app](https://zonetwo.vercel.app)** · not affiliated with Garmin.

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

## Install (Claude Desktop)

Download **zonetwo.mcpb** from
[zonetwo.vercel.app](https://zonetwo.vercel.app) or the
[latest release](https://github.com/alchemist-s/zonetwo/releases/latest), and
open it. Claude Desktop shows an install dialog asking for your Garmin email
and password. Nothing else is needed — no Python, no terminal, no config files.

If your account uses two-step verification, Garmin emails you a code the first
time Claude reads your data. Paste it into the conversation and Claude will
finish signing in. The code lasts 30 minutes, and the saved token then keeps
you signed in for about a year.

Your password is used once, to obtain that token. It is stored by Claude
Desktop's own configuration, not by this server, and is only re-used if the
token is ever rejected.

## Setup (from source)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```sh
git clone https://github.com/alchemist-s/zonetwo.git
cd zonetwo
uv sync
uv run zonetwo login     # prompts for email, password, and MFA code if enabled
uv run zonetwo status    # confirms the tokens work
```

`login` must run in a real terminal — it prompts for a password and, if your
account has MFA, a code. It cannot run through a non-interactive shell (such as
Claude Code's `!` prefix), and says so rather than failing with a traceback.

With MFA off, credentials can come from the environment instead:

```sh
GARMIN_EMAIL=you@example.com GARMIN_PASSWORD=... uv run zonetwo login
```

Garmin rate-limits login attempts by IP and can answer `429` on the first
strategy the client tries. The library falls back across several; if the whole
attempt fails that way, wait a few minutes rather than retrying immediately.

Tokens land in `~/.garminconnect` (override with `GARMINTOKENS`), written
`0600` in a `0700` directory.

### Wire it into Claude Code

```sh
claude mcp add zonetwo --scope user -- uv --directory /absolute/path/to/zonetwo run zonetwo
```

Use an absolute path — the stored config does not expand `~`. `--scope user`
makes the server available in every project; without it the default `local`
scope binds it to whichever directory you ran the command from.

### Wire it into Claude Desktop

In `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/zonetwo", "run", "zonetwo"]
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
| `garmin_briefing` | Sleep, HRV, stress, readiness, Body Battery and daily totals in one call |
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

### Units travel in the field name

Garmin reports distances in metres, durations in seconds, speeds in metres per
second and body mass in **grams**, all unlabelled. A bare `"weight": 74500`
reads as kilograms to anything summarising it, and `"averageSpeed": 2.75` reads
as km/h. So fields are renamed to carry their unit — `distanceMeters`,
`durationSeconds`, `averageSpeedMetersPerSecond`, `weightKg` — and grams are
converted, because that one is wrong rather than merely ambiguous. Foot-based
activities also get a derived `pace` ("5:59 min/km"), which is the number a
runner actually reads.

### Rate limits

Garmin answers `429` rather than queuing, per IP. Calls are capped at three in
flight and retried with exponential backoff and jitter; the semaphore is
released before sleeping, so one throttled call does not stall unrelated ones.
A 429 that survives the retry budget reaches the caller as a message saying to
wait, not as a generic failure.

### Why the responses are trimmed

Garmin's payloads are built for a dashboard: one night of sleep carries
thousands of per-minute samples, and a 20-activity list runs past 100 kB of
mostly-null fields. Every tool returns a compact projection and keeps the raw
payload behind `raw=true`. Anything still over ~60 kB comes back as an explicit
`truncated` marker rather than cut-off JSON — truncated JSON reads as complete
data that happens to end early, and gets summarised as fact.

### What you get depends on your watch

Nothing here is tied to a particular account — log in with yours and it works.
But Garmin computes different metrics on different hardware, and the API returns
an empty response rather than an error for the ones your device does not
support. On a Venu 3, for instance, `garmin_training_readiness` returns a
literal `[]` and `garmin_training_status` comes back with every field null:
those are Forerunner/Fenix features. `zonetwo check` reports that as `none`
rather than `ok`, so you can tell "my watch doesn't do this" from "this is
broken".

### Writes

Read-only by default. Setting `GARMIN_MCP_ENABLE_WRITES=1` adds
`garmin_rename_activity`, `garmin_add_weight` and `garmin_add_hydration`.
Nothing deletes, by design — these tools act on a real health record that syncs
back to the watch.

## Privacy

Everything runs on your own computer. Requests go straight from your machine to
Garmin: there is no server operated by the author, no telemetry, no analytics
and no error reporting.

**Your password** is used once, to obtain an access token. The token is saved at
`~/.garminconnect` (mode `0600` inside a `0700` directory) and the password is
not used again unless that token is rejected — after about a year, or if you
change your Garmin password. The password is never written to disk by this
server, never logged, and goes nowhere except Garmin's own sign-in service.

**Your health data** is fetched on demand and never cached. Note that anything
you ask about becomes part of your Claude conversation, which Anthropic
processes under [their privacy policy](https://www.anthropic.com/legal/privacy).

**Scope.** Read-only unless `GARMIN_MCP_ENABLE_WRITES=1` (or the equivalent
install option). Even then it can only rename an activity and record weight or
hydration — no tool for deleting anything exists in this server.

**Removing it.** Uninstall the extension, delete `~/.garminconnect`, or change
your Garmin password to invalidate the token from Garmin's side.

Not intended for use by anyone under 16.

## Troubleshooting

- **"No Garmin tokens…"** — run `zonetwo login`.
- **Tokens rejected** — they expire after about a year, and a password change
  invalidates them. `zonetwo login --force`.
- **Rate-limited** — Garmin throttles per account. Wait a few minutes; avoid
  looping over long date ranges a day at a time.
- **Today's numbers look wrong** — Garmin only has what the watch last synced to
  your phone.
- **China accounts** — `zonetwo login --china`.

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
uv run zonetwo login     # once
uv run zonetwo check
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
uv run mcp dev src/zonetwo/server.py:mcp --with-editable .
```

Opens a UI where you can list tools, read their schemas, and call them with
your own arguments. Needs `npx`. The `--with-editable .` matters: the Inspector
runs the server in its own environment, which otherwise lacks `garminconnect`.

**4. End to end in Claude Code.**

```sh
claude mcp add zonetwo --scope user -- uv --directory /absolute/path/to/zonetwo run zonetwo
claude mcp list          # should show garmin as connected
```

Then ask something that needs real data — "how did I sleep last night?", "what
was my longest run this month?", "is my HRV trending down?" — and check the
numbers against the Garmin Connect app.

To test the write tools, add `-e GARMIN_MCP_ENABLE_WRITES=1` to the
`claude mcp add` command.
