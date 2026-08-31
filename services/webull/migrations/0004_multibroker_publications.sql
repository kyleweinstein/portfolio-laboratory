CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Additive provider-neutral expansion. Existing Webull text identifiers remain
-- in place until every caller has moved to the internal UUID columns.
ALTER TABLE brokerage_accounts
    DROP CONSTRAINT IF EXISTS brokerage_accounts_provider_check;
ALTER TABLE brokerage_accounts
    ADD CONSTRAINT brokerage_accounts_provider_check
    CHECK (provider IN ('webull', 'plaid_m1', 'schwab'));

ALTER TABLE service_connection_state
    DROP CONSTRAINT IF EXISTS service_connection_state_provider_check;
ALTER TABLE service_connection_state
    ADD CONSTRAINT service_connection_state_provider_check
    CHECK (provider IN ('webull', 'plaid_m1', 'schwab'));

ALTER TABLE verification_attempts
    DROP CONSTRAINT IF EXISTS verification_attempts_provider_check;
ALTER TABLE verification_attempts
    ADD CONSTRAINT verification_attempts_provider_check
    CHECK (provider IN ('webull', 'plaid_m1', 'schwab'));

ALTER TABLE brokerage_accounts
    ADD COLUMN IF NOT EXISTS internal_account_id UUID;
ALTER TABLE brokerage_accounts
    ADD COLUMN IF NOT EXISTS provider_account_id TEXT;

UPDATE brokerage_accounts
SET internal_account_id = MD5(provider || ':' || account_id)::UUID
WHERE internal_account_id IS NULL;
UPDATE brokerage_accounts
SET provider_account_id = account_id
WHERE provider_account_id IS NULL;

ALTER TABLE brokerage_accounts
    ALTER COLUMN internal_account_id SET NOT NULL;
ALTER TABLE brokerage_accounts
    ALTER COLUMN provider_account_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS brokerage_accounts_internal_id_idx
    ON brokerage_accounts (internal_account_id);
CREATE UNIQUE INDEX IF NOT EXISTS brokerage_accounts_provider_external_idx
    ON brokerage_accounts (provider, provider_account_id);

CREATE TABLE IF NOT EXISTS broker_connections (
    connection_id UUID PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider IN ('webull', 'plaid_m1', 'schwab')),
    credential_reference TEXT,
    institution_id TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('CONFIGURATION_REQUIRED', 'READY', 'REAUTH_REQUIRED', 'ERROR', 'DISCONNECTED')
    ),
    capabilities JSONB NOT NULL DEFAULT '{}'::JSONB
        CHECK (JSONB_TYPEOF(capabilities) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS broker_connection_credentials (
    connection_id UUID PRIMARY KEY
        REFERENCES broker_connections(connection_id) ON DELETE CASCADE,
    encrypted_access_token BYTEA NOT NULL,
    encrypted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

REVOKE ALL ON broker_connection_credentials FROM PUBLIC;

ALTER TABLE brokerage_accounts
    ADD COLUMN IF NOT EXISTS connection_id UUID REFERENCES broker_connections(connection_id)
        ON DELETE SET NULL;

INSERT INTO broker_connections (
    connection_id, provider, status, capabilities
)
SELECT
    MD5('portfolio-lab:connection:webull')::UUID,
    'webull',
    CASE WHEN connected THEN 'READY' ELSE 'DISCONNECTED' END,
    '{"accounts":true,"balances":true,"positions":true,"activities":true}'::JSONB
FROM service_connection_state
WHERE provider = 'webull'
ON CONFLICT (connection_id) DO NOTHING;

UPDATE brokerage_accounts
SET connection_id = MD5('portfolio-lab:connection:webull')::UUID
WHERE provider = 'webull' AND connection_id IS NULL;

ALTER TABLE service_connection_state
    ADD COLUMN IF NOT EXISTS selected_internal_account_id UUID;
ALTER TABLE service_connection_state
    DROP CONSTRAINT IF EXISTS service_connection_state_selected_internal_account_fkey;
ALTER TABLE service_connection_state
    ADD CONSTRAINT service_connection_state_selected_internal_account_fkey
    FOREIGN KEY (selected_internal_account_id)
    REFERENCES brokerage_accounts(internal_account_id) ON DELETE SET NULL;

UPDATE service_connection_state state
SET selected_internal_account_id = account.internal_account_id
FROM brokerage_accounts account
WHERE state.provider = account.provider
  AND state.selected_account_id = account.account_id
  AND state.selected_internal_account_id IS NULL;

ALTER TABLE sync_runs ADD COLUMN IF NOT EXISTS internal_account_id UUID;
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS internal_account_id UUID;
ALTER TABLE portfolio_snapshots ADD COLUMN IF NOT EXISTS cash_source TEXT
    NOT NULL DEFAULT 'explicit'
    CHECK (cash_source IN ('explicit', 'derived_residual', 'unavailable'));
ALTER TABLE position_snapshots ADD COLUMN IF NOT EXISTS internal_account_id UUID;
ALTER TABLE cash_activities ADD COLUMN IF NOT EXISTS internal_account_id UUID;
ALTER TABLE order_records ADD COLUMN IF NOT EXISTS internal_account_id UUID;
ALTER TABLE statement_anchors ADD COLUMN IF NOT EXISTS internal_account_id UUID;

UPDATE sync_runs child
SET internal_account_id = parent.internal_account_id
FROM brokerage_accounts parent
WHERE child.account_id = parent.account_id AND child.internal_account_id IS NULL;
UPDATE portfolio_snapshots child
SET internal_account_id = parent.internal_account_id
FROM brokerage_accounts parent
WHERE child.account_id = parent.account_id AND child.internal_account_id IS NULL;
UPDATE position_snapshots child
SET internal_account_id = parent.internal_account_id
FROM brokerage_accounts parent
WHERE child.account_id = parent.account_id AND child.internal_account_id IS NULL;
UPDATE cash_activities child
SET internal_account_id = parent.internal_account_id
FROM brokerage_accounts parent
WHERE child.account_id = parent.account_id AND child.internal_account_id IS NULL;
UPDATE order_records child
SET internal_account_id = parent.internal_account_id
FROM brokerage_accounts parent
WHERE child.account_id = parent.account_id AND child.internal_account_id IS NULL;
UPDATE statement_anchors child
SET internal_account_id = parent.internal_account_id
FROM brokerage_accounts parent
WHERE child.account_id = parent.account_id AND child.internal_account_id IS NULL;

CREATE INDEX IF NOT EXISTS sync_runs_internal_account_idx
    ON sync_runs (internal_account_id, completed_at DESC);
CREATE INDEX IF NOT EXISTS portfolio_snapshots_internal_account_idx
    ON portfolio_snapshots (internal_account_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS position_snapshots_internal_account_idx
    ON position_snapshots (internal_account_id, symbol);
CREATE INDEX IF NOT EXISTS cash_activities_internal_account_idx
    ON cash_activities (internal_account_id, occurred_at DESC);

ALTER TABLE statement_anchors
    DROP CONSTRAINT IF EXISTS statement_anchors_ending_equity_check;

CREATE TABLE IF NOT EXISTS published_portfolios (
    publication_id UUID PRIMARY KEY,
    internal_account_id UUID NOT NULL UNIQUE
        REFERENCES brokerage_accounts(internal_account_id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider IN ('webull', 'plaid_m1', 'schwab')),
    slug VARCHAR(80) NOT NULL UNIQUE
        CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    title VARCHAR(120) NOT NULL,
    benchmark_symbol VARCHAR(32) NOT NULL DEFAULT 'SPY',
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    active_revision_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS publication_revisions (
    revision_id UUID PRIMARY KEY,
    publication_id UUID NOT NULL
        REFERENCES published_portfolios(publication_id) ON DELETE CASCADE,
    source_snapshot_id UUID NOT NULL
        REFERENCES portfolio_snapshots(snapshot_id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK (state IN ('PUBLISHED', 'SUPERSEDED')),
    holdings_as_of TIMESTAMPTZ NOT NULL,
    performance_through TIMESTAMPTZ,
    ytd_return_percent NUMERIC(20, 10),
    gross_exposure_percent NUMERIC(20, 10),
    net_exposure_percent NUMERIC(20, 10),
    analytical_sleeve_percent NUMERIC(20, 10),
    quality TEXT NOT NULL CHECK (
        quality IN (
            'broker_reported', 'statement_reconciled', 'portfolio_lab_computed',
            'estimated', 'unavailable'
        )
    ),
    analytics JSONB CHECK (analytics IS NULL OR JSONB_TYPEOF(analytics) = 'object'),
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE published_portfolios
    DROP CONSTRAINT IF EXISTS published_portfolios_active_revision_fkey;
ALTER TABLE published_portfolios
    ADD CONSTRAINT published_portfolios_active_revision_fkey
    FOREIGN KEY (active_revision_id) REFERENCES publication_revisions(revision_id)
    ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;

CREATE UNIQUE INDEX IF NOT EXISTS publication_revisions_one_published_idx
    ON publication_revisions (publication_id) WHERE state = 'PUBLISHED';
CREATE INDEX IF NOT EXISTS publication_revisions_history_idx
    ON publication_revisions (publication_id, published_at DESC);

CREATE TABLE IF NOT EXISTS published_holdings (
    revision_id UUID NOT NULL
        REFERENCES publication_revisions(revision_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    kind TEXT NOT NULL CHECK (kind IN ('security', 'cash_margin', 'other')),
    symbol VARCHAR(32),
    name VARCHAR(160) NOT NULL,
    weight_percent NUMERIC(20, 10),
    cost_basis_per_share NUMERIC(38, 12),
    return_percent NUMERIC(20, 10),
    quality TEXT NOT NULL CHECK (
        quality IN (
            'broker_reported', 'statement_reconciled', 'portfolio_lab_computed',
            'estimated', 'unavailable'
        )
    ),
    PRIMARY KEY (revision_id, ordinal),
    CHECK (
        (kind = 'security') OR
        (cost_basis_per_share IS NULL AND return_percent IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS published_performance_points (
    revision_id UUID NOT NULL
        REFERENCES publication_revisions(revision_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    at TIMESTAMPTZ NOT NULL,
    return_percent NUMERIC(20, 10) NOT NULL,
    benchmark_return_percent NUMERIC(20, 10),
    quality TEXT NOT NULL CHECK (
        quality IN (
            'broker_reported', 'statement_reconciled', 'portfolio_lab_computed',
            'estimated', 'unavailable'
        )
    ),
    PRIMARY KEY (revision_id, ordinal)
);

CREATE INDEX IF NOT EXISTS published_performance_points_time_idx
    ON published_performance_points (revision_id, at);
