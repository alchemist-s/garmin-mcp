# Changelog

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
