# Portfolio Lab Webull service

Private read-only adapter service for Portfolio Lab. Its public Python interface
contains account, balance, position, cash-activity and historical-order reads only;
there are no order preview, placement, replacement or cancellation methods.

## Runtime configuration

Supply all sensitive values through Railway environment variables or a mounted
runtime token directory. Never commit them.

- `DATABASE_URL`: Railway Postgres connection string.
- `DISCORD_SESSION_ENCRYPTION_KEY`: a separate high-entropy secret used only to
  encrypt Discord access and refresh tokens in `discord_viewer_sessions`. The
  follower browser receives only a random opaque session ID; configure the
  Vinext service with the private `*.railway.internal` broker-service URL and
  existing internal bearer/owner variables.
- `INTERNAL_API_TOKEN`: bearer token shared only with the Portfolio Lab proxy.
- `PORTFOLIO_OWNER_GITHUB_ID`: expected value of `x-portfolio-owner-github-id`.
- `WEBULL_APP_KEY` and `WEBULL_APP_SECRET`: approved Webull OpenAPI credentials.
- `WEBULL_OPENAPI_TOKEN_DIR`: mounted directory used by the official SDK for its
  verified access token.
- `WEBULL_READ_ONLY_ADAPTER_ENABLED`: fail-closed application activation gate.
  Set to `true` only after reviewing that Portfolio Lab exposes account, asset,
  activity and order-history reads only. Webull's Retail Trading API does not
  document a separate query-only permission for individual App Keys, so this
  enables an application-enforced boundary, not a broker-enforced key scope.
- `WEBULL_REGION` (default `us`) and `WEBULL_API_ENDPOINT` (default
  `api.webull.com`).
- `CASH_ACTIVITY_LOOKBACK_DAYS` (default `45`), `SYNC_INTERVAL_SECONDS`
  (default `900`), and `AUTO_MIGRATE` (default `true`).
- `PLAID_M1_INTEGRATION_ENABLED`: fail-closed production gate for Plaid
  Investments. Plaid server credentials, redirect URI, environment, and optional
  approved M1 institution ID use `PLAID_CLIENT_ID`, `PLAID_SECRET`,
  `PLAID_REDIRECT_URI`, `PLAID_ENV`, and `PLAID_M1_INSTITUTION_ID`.
- `BROKER_CREDENTIAL_ENCRYPTION_KEY`: Railway-only key used by PostgreSQL
  `pgcrypto` to encrypt provider OAuth access and refresh tokens. Token expiry is
  stored alongside the ciphertext so refresh decisions do not expose token
  material. Tokens are never returned by an API, stored in logs, or placed in
  publication tables.
- `M1_STATEMENT_IMPORT_ENABLED` (default `false`) gates the operator-only M1/Apex
  normalized statement backfill boundary. `M1_STATEMENT_OUTPUT_KEY` is the
  Railway-held Fernet key shared with the offline parser output step. Keep both
  values out of the browser service; enabling this feature does not create a
  browser upload surface.
- `PORTFOLIO_PUBLICATION_ENABLED` and
  `AUTO_PUBLISH_AFTER_CLOSE_ENABLED`: independent fail-closed gates for the
  follower projection and its after-close revision switch.
- `PUBLICATION_WEIGHT_TOLERANCE_BPS` (default `5`) controls signed allocation
  reconciliation. `SCHWAB_INTEGRATION_ENABLED` remains false while the Schwab
  contract is readiness-only.

## Schwab readiness contract (disabled)

`SCHWAB_INTEGRATION_ENABLED=false` is intentional. The repository contains only
the pieces that can be reviewed safely before a live connector exists:

- provider-neutral encrypted access token, refresh token, access-token expiry,
  and refresh-token expiry storage;
- an injected `SchwabReadClient` protocol containing account, balance/position,
  and transaction reads only;
- a mandatory `SchwabRequestThrottle` acquisition before every injected read;
- pure mappings for broker account value, signed cash or margin, positions and
  per-share average cost, plus explicit external-flow transaction types.

There is no production Schwab HTTP client, OAuth callback, token exchange route,
service factory wiring, scheduled synchronization, order method, trading route,
or money-movement method. A later release must separately review Schwab's current
OAuth and rate-limit documentation, implement token refresh with rotation-safe
transactions, reconcile fixture payloads against a real read-only account, and
add a second fail-closed release gate before enabling live reads.
- `PUBLISHED_ANALYTICS_ENABLED` (default `true`) generates the stored,
  privacy-safe current-holdings model during preview and publication. Its
  manual-equivalent defaults are controlled by `PUBLISHED_ANALYTICS_YEARS` (3),
  `PUBLISHED_ANALYTICS_RISK_FREE_RATE` (0.04),
  `PUBLISHED_ANALYTICS_REBALANCE_BAND_PERCENT` (2.5),
  `PUBLISHED_ANALYTICS_DRIFT_DAYS` (63), and bounded fetch concurrency.

Run the API with `python -m webull_service.serve`. Configure a separate Railway
cron service to run `python -m webull_service.cron_trigger` every 15 minutes. The
trigger safely no-ops outside 9:30 a.m.-4:20 p.m. U.S. Eastern on weekdays and
makes one authenticated request to the private API; manual API sync remains
available at any time.

The cron service needs only `WEBULL_SERVICE_URL`, `INTERNAL_API_TOKEN`, and
`PORTFOLIO_OWNER_GITHUB_ID`. Point `WEBULL_SERVICE_URL` at the API service's
private Railway URL. Use the same bearer token and owner ID on both services.
The trigger accepts only a root URL on loopback or a `.railway.internal` host
and refuses redirects. Do not give the cron service `WEBULL_APP_KEY`,
`WEBULL_APP_SECRET`, `WEBULL_OPENAPI_TOKEN_DIR`, `DATABASE_URL`, or a persistent
volume.

## First connection and token persistence

Mount a private persistent Railway volume at `/data/webull-token` on the API
service only and set its `WEBULL_OPENAPI_TOKEN_DIR=/data/webull-token`. The API
service is the only service that receives Webull credentials or token-volume
access. Until the application adapter is enabled, the owner dashboard reports
**Read-only activation pending** and does not offer a misleading verification action.
Webull's first approval can wait for in-app verification for up to five minutes.
After the gate is enabled, start it once with **Verify configured Webull account**
and approve it in Webull. The dashboard persists the attempt stage, start time,
last update, terminal result, and next action in PostgreSQL, so the page may be
closed and reopened without losing status. Repeated starts reuse the active
attempt, and an interrupted attempt becomes retryable after its seven-minute
lease expires. The saved Webull token then survives API restarts.

Before that first request, review the private adapter and server routes to
confirm that they expose no order preview, placement, replacement,
cancellation, transfer or withdrawal operation. Then set
`WEBULL_READ_ONLY_ADAPTER_ENABLED=true`. The approved Retail App Key itself may
retain broader Trading API authority, so keep the private service isolated,
preserve owner-only authentication, and rotate the credential if it may have
been exposed. Never paste the App Key, Secret or token into a browser form,
repository, log or chat message.

## Proxy contract

`GET /health` is unauthenticated and never returns secrets. Every `/v1` route
requires both `Authorization: Bearer <INTERNAL_API_TOKEN>` and
`x-portfolio-owner-github-id`. JSON fields use camelCase.

- `GET /v1/status`: accounts, `selectedAccountId`, `lastSyncedAt`, dashboard,
  durable `verification` details, structured `lastSyncAttempt` quality status,
  and an explicit `nextAction`.
- `POST /v1/connect`, `DELETE /v1/connect`.
- `POST /v1/accounts/select` with `{ "accountId": "..." }`.
- `POST /v1/sync` and `POST /v1/backfill` with optional `accountId`.
- `POST /v1/scheduled-sync`: private cron target that syncs every exposed
  connected account after enforcing the read-only activation gate. It safely
  no-ops before the owner completes the first connection.
- `GET /v1/portfolio`, `/v1/activities`, `/v1/orders`, and `/v1/issues`.
- `GET /v1/capabilities?live=true` performs only non-mutating capability calls.
- `GET /v1/providers` and `GET /v1/providers/{provider}/status` return
  non-secret provider readiness. `GET /v1/providers/{provider}/accounts` returns
  opaque internal account handles rather than broker account identifiers.
- Plaid owner flow: `POST /v1/providers/plaid/link-token`, `POST
  /v1/providers/plaid/exchange`, and `POST
  /v1/providers/plaid/update-link-token`. The exchange response contains status
  only; the access token is encrypted in PostgreSQL. `POST
  /v1/providers/plaid/sync` reads M1 Investments accounts, holdings, and
  transactions. The private `POST /v1/providers/plaid/webhook` accepts only the
  normalized event forwarded by a public proxy after Plaid signature validation.
- Publication owner flow: `PUT /v1/publications/configure`, `GET
  /v1/publications/manage`, `GET
  /v1/publications/{publicationId}/preview`, `POST
  /v1/publications/{publicationId}/publish`, and `DELETE
  /v1/publications/{publicationId}`. Configure with the opaque `accountHandle`.
  Preview runs the same privacy checks and analytics build but does not create
  or switch a follower revision. Publish accepts an empty JSON object; browser
  analytics payloads are rejected because analytics are generated server-side.
- Operator statement flow: `POST /v1/statement-imports` accepts only a bounded
  `application/octet-stream` Fernet artifact after the normal bearer and owner
  checks. It is hidden while `M1_STATEMENT_IMPORT_ENABLED=false`, decrypts with
  the service-only `M1_STATEMENT_OUTPUT_KEY`, resolves the exact owner-scoped M1
  account handle, and returns an amount-free receipt. There is deliberately no
  Vinext/browser proxy for this route.
- Follower projection: `GET /v1/publications` and `GET
  /v1/publications/{slug}`. These strict DTOs contain percentage returns, signed
  weights, and optional per-share basis only. They contain no quantities,
  account identifiers, balances, position values, dollar P&L, or cash-flow
  amounts. The Vinext proxy applies Discord authorization before forwarding them.

Publication revisions are written transactionally. The preceding active
revision is superseded and the pointer is switched in the same transaction, so
any validation or persistence failure rolls back to the last good follower
snapshot. Positive cash, negative margin cash, and unexplained residual
assets/liabilities are separate signed rows; when net account value is zero or
negative, weights and exposures are unavailable instead of divided. YTD remains
unavailable until a year-start performance anchor exists.

Each successful revision also stores fixed-sleeve risk statistics,
style/sector/factor classifications, a packed correlation map, top pair
insights, direction comparison, rebalance buckets, and a nonnegative 60%-capped
maximum-Sharpe scenario. Required holding or benchmark history failure aborts
the new revision and retains the preceding one; optional proxy failures degrade
only the affected classifications. These modeled analytics use public adjusted
closes and eligible positive stocks/ETFs, never account identifiers, quantities,
position values, balances, or dollar P&L.
Account-performance points receive cumulative adjusted-close benchmark returns
only when every published valuation date has an exact benchmark close. Missing
holiday, weekend, or out-of-window anchors leave the benchmark series unavailable
instead of shifting dates. Scheduled publication regenerates analytics while the
feature is enabled and carries the preceding typed analytics forward when a
rollout temporarily disables recalculation.

Disconnect purges imported account data from Postgres. It does not modify the SDK
token directory; token revocation remains a Webull account-management action.

## Statement-import contract

The former raw `POST /v1/statement-anchors` route is not exposed. Generate and
validate normalized private artifacts offline with `tools/m1-statements`, then
send the resulting `*.json.fernet` bytes from a trusted operator/Railway job to
`POST /v1/statement-imports` as `application/octet-stream`. The service never
accepts or stores PDFs, unrestricted extracted text, or the encrypted request
body. Batch IDs and source hashes are idempotent: an identical repeated batch
returns `already_imported`, while changed content under an existing batch or
source identifier aborts the whole transaction.

The response contains only the batch ID, original opaque account handle, record
counts, coverage dates, import status, and conservative publication eligibility.
Anchor values, cash/margin evidence, external-flow amounts, account IDs, and
provider account identifiers are never returned by this endpoint or written to
logs.

Only snapshots captured at or after 4:00 p.m. U.S. Eastern participate in
historical returns. Intraday snapshots remain available for current holdings and
Webull-reported point-in-time metrics, but cannot masquerade as reconciled daily
performance.

Balance and positions form the atomic current-holdings snapshot. Cash activity is
an optional upstream capability: if it is unavailable, current holdings still
refresh, `lastSyncAttempt.cashActivitiesComplete` is `false`, and performance is
withheld with a data-quality warning until a later successful activity sync or
backfill fully covers the unresolved time range. A shorter routine refresh cannot
erase a gap discovered by a longer backfill. Core account, balance, position, and
database failures remain hard sync errors.
