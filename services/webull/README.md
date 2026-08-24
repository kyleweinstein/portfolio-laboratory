# Portfolio Lab Webull service

Private read-only adapter service for Portfolio Lab. Its public Python interface
contains account, balance, position, cash-activity and historical-order reads only;
there are no order preview, placement, replacement or cancellation methods.

## Runtime configuration

Supply all sensitive values through Railway environment variables or a mounted
runtime token directory. Never commit them.

- `DATABASE_URL`: Railway Postgres connection string.
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

Disconnect purges imported account data from Postgres. It does not modify the SDK
token directory; token revocation remains a Webull account-management action.

## Statement-anchor contract

`POST /v1/statement-anchors` accepts a user-verified normalized total:

```json
{
  "accountId": "...",
  "externalStatementId": "2026-07",
  "statementDate": "2026-07-31T20:00:00Z",
  "endingEquity": "125000.00",
  "endingCash": "2500.00",
  "currency": "USD",
  "sourceSha256": "<optional 64-character SHA-256>"
}
```

The service never accepts, stores, downloads or parses a PDF. Anchors participate
in daily Modified Dietz/TWR and XIRR calculations alongside scheduled snapshots.

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
