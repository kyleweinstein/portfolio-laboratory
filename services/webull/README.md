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
- `WEBULL_REGION` (default `us`) and `WEBULL_API_ENDPOINT` (default
  `api.webull.com`).
- `CASH_ACTIVITY_LOOKBACK_DAYS` (default `45`), `SYNC_INTERVAL_SECONDS`
  (default `900`), and `AUTO_MIGRATE` (default `true`).

Run the API with `python -m webull_service.serve`. Run one scheduled cycle with
`python -m webull_service.scheduler`, or a dedicated worker with
`python -m webull_service.scheduler --loop`. A Railway cron can invoke the
one-shot command every 15 minutes; it safely no-ops until credentials exist and
outside 9:30 a.m.-4:20 p.m. U.S. Eastern on weekdays. Manual API sync remains
available at any time.

## Proxy contract

`GET /health` is unauthenticated and never returns secrets. Every `/v1` route
requires both `Authorization: Bearer <INTERNAL_API_TOKEN>` and
`x-portfolio-owner-github-id`. JSON fields use camelCase.

- `GET /v1/status`: accounts, `selectedAccountId`, `lastSyncedAt`, and dashboard.
- `POST /v1/connect`, `DELETE /v1/connect`.
- `POST /v1/accounts/select` with `{ "accountId": "..." }`.
- `POST /v1/sync` and `POST /v1/backfill` with optional `accountId`.
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
