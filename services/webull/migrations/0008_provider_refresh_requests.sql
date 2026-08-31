CREATE TABLE IF NOT EXISTS provider_refresh_requests (
    provider TEXT PRIMARY KEY CHECK (provider IN ('plaid_m1', 'schwab')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

REVOKE ALL ON provider_refresh_requests FROM PUBLIC;

CREATE INDEX IF NOT EXISTS provider_refresh_requests_pending_idx
    ON provider_refresh_requests (requested_at)
    WHERE processed_at IS NULL;
