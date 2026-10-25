# Local report services and deployment boundary

Use the locked engine environment and Node 22. Public dashboard configuration contains
only hub/database/asset coordinates; engine and service tokens stay server-side.

## Local phone service

1. Run the existing local hub acceptance setup in `DASHBOARD_PHASE_E.md`. Publish a
   new disposable database, seed exactly one scenario with `tools/seed_dashboard_hub.py`,
   and retain its credentials JSON privately with mode 0600. Never reset a shared hub.
2. Set server environment: `STDB_URI`, `STDB_DB`, `STDB_SERVICE_TOKEN`,
   `STDB_SCENARIO_ID` and a random `REPORT_SESSION_SECRET` of at least 32 characters.
   Keep `VOICE_REPORT_ENABLED=false`. The local adapter does not load `.env` implicitly.
   `DEV_SERVICE_PORT` defaults to 8797. Run `node tools/dev_services.mjs`.
3. Start Vite with `EMBERLINE_DEV_SERVICE_PROXY=http://127.0.0.1:8797`,
   `VITE_STDB_URI=http://127.0.0.1:3017`, and `VITE_STDB_DB` matching that database.
   Use the same scenario/assets as the dashboard. Visit `/p?j=JOIN_CODE`, where the
   join code comes from that scenario's `spawn_points.json` keys.
4. Join, then tap Fire/Smoke/Embers/All clear. Controls use the assigned location and
   shared clock. Submission confirms storage, not assimilation. The real worker must
   process the report command and publish an accepted/rejected status. No worker means
   the page correctly stays at "awaiting engine review".
5. Run the engine in `--profile offline` against this local hub to prevent outbound
   notifications. `--hub-uri`, `--cache-root`, `--workers`, `--memory-gb` and `--timeout-s`
   select its explicit local runtime configuration. Use an independent runtime cache;
   never run two writers against the same precompute root. A `--bundle-root` replay
   has no recomputing model and explicitly rejects injected report branches.

Report sessions expire after two hours and are kept in browser session storage. A
lost connection hides controls; reload to reconnect. Report request IDs prevent
duplicate writes; engine acceptance additionally enforces 60 wall-seconds per person.
Concurrent web instances can race the preliminary 20-second UI/service throttle;
the worker remains authoritative for actual assimilation. The dispatch journal
prevents automatic redial after an uncertain request; reconcile unknown provider
outcomes manually before any authorized retry.

## Optional voice and callbacks

Voice additionally requires `VOICE_REPORT_ENABLED=true`, `XAI_API_KEY` and
`ANTHROPIC_API_KEY` on the service host. Its upload accepts up to 15 seconds of mono
16 kHz, 16-bit PCM WAV. Audio goes to xAI transcription; text goes to an enum-only
Anthropic classifier. Coordinates/time come from validated context, never the LLM.
Audio is not written to disk. Transcripts remain in the hub's private transcript
table until reset. This pipeline has been tested with provider stubs only.

Cartesia tools require `CARTESIA_WEBHOOK_SECRET` via
`x-cartesia-webhook-secret`. Configure the provider to send that header; actual
provider compatibility and a selected authorized recipient must be rehearsed before
delivery is claimed. Callback tools only read bounded alert context or acknowledge
an approved/in-flight/completed call. They do not initiate calls themselves.

## Hosting

The handlers in `web/api/` target a Node/Vercel runtime. A deployed host must route
`/api/*` to them and rewrite `/p` to the SPA. GitHub Pages hosts only static assets;
it cannot execute these functions. Do not enable a static site's report controls by
embedding a service token or bypassing authentication. Deployment and actual provider
rehearsal are separate outstanding work.

## Checks

```sh
npm --prefix web run test:services
uv run --project engine pytest engine/tests/test_report_branch.py engine/tests/test_release_runtime.py -q
```

The `dashboard-hub.yml` workflow publishes a separate fresh `release-report-ci`
database and runs `test_report_hub.py` with `EMBERLINE_REPORT_HUB_TESTS=1`. It checks
native raw-member export, a real report command, accepted/assimilated child evidence
and unchanged parent cycles. It sends no external notification.
