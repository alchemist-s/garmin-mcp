# Changelog

## 0.4.0

Plans, not just readouts.

- `garmin_create_workout` builds structured sessions — intervals, repeats, pace
  or heart-rate targets — and schedules them onto the watch. Steps are written
  in plain terms and translated into Garmin's nested format, because no model
  should be hand-writing four nested dictionaries per step.
- `garmin_training_history` returns weekly volume, longest run and pace over N
  weeks in one call, so writing a plan does not mean fetching runs one range at
  a time.
- `garmin_export_activities` returns a date range as CSV.
- Scheduling and unscheduling workouts, and deleting saved workouts, are now
  always available: plans are drafts. Editing records stays opt-in, renamed to
  `ZONETWO_ENABLE_WRITES`. Recorded activities still cannot be deleted at all.
- Transient connection errors are retried alongside 429s — Garmin reads time
  out on wide date ranges often enough to be routine.
- Rebuilt the website around building a plan rather than reading a number.

## 0.3.0

Renamed to **Zone Two**. The package, the CLI command and the bundle are now
`zonetwo`; the repository moved to `alchemist-s/zonetwo` and the site to
zonetwo.vercel.app. Garmin is named only where it describes what the software
connects to, never as branding.

- Rebuilt the website: an example conversation, a visual identity, and a
  trademark disclaimer.
- The CLI is `zonetwo` rather than `garmin-mcp`. Tool names keep their
  `garmin_` prefix, since they describe the data source rather than the product.

## 0.2.1

- Point the bundle's privacy policy at the published site.

## 0.2.0

**Breaking:** response fields now carry their unit in the name.
`distance` became `distanceMeters`, `duration` became `durationSeconds`,
`averageSpeed` became `averageSpeedMetersPerSecond`, and `weight` became
`weightKg` — converted from the grams Garmin actually sends. Foot-based
activities gained a derived `pace`.

- Added `garmin_briefing`: sleep, HRV, stress, readiness, Body Battery and
  daily totals fetched concurrently in one call. A failing section is named in
  `sectionsUnavailable`; one with no reading is named in `sectionsNoData`.
- Rate limiting: calls are capped at three in flight and retried with
  exponential backoff, since Garmin answers 429 rather than queuing.
- Added a Claude Desktop bundle (`.mcpb`). The install dialog collects Garmin
  credentials; two-step verification is completed in the conversation via
  `garmin_submit_mfa_code`.
- Sign-in falls back to credentials when a token store is rejected, instead of
  demanding the CLI.
- `garmin-mcp login` fails legibly without a terminal, and accepts
  `GARMIN_EMAIL` / `GARMIN_PASSWORD`.
- Added the MIT licence.

## 0.1.0

Initial release: 25 read-only tools over Garmin Connect, token-based auth kept
out of the server, and `garmin-mcp check` for a live smoke test.
