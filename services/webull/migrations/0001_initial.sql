CREATE TABLE IF NOT EXISTS brokerage_accounts (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'webull' CHECK (provider = 'webull'),
    account_type TEXT NOT NULL,
    status TEXT NOT NULL,
    currency TEXT NOT NULL,
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS service_connection_state (
    provider TEXT PRIMARY KEY CHECK (provider = 'webull'),
    connected BOOLEAN NOT NULL DEFAULT FALSE,
    selected_account_id TEXT,
    connected_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO service_connection_state (provider, connected)
VALUES ('webull', FALSE)
ON CONFLICT (provider) DO NOTHING;

CREATE TABLE IF NOT EXISTS sync_runs (
    sync_run_id UUID PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES brokerage_accounts(account_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'ERROR')),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    snapshot_id UUID,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS sync_runs_account_completed_idx
    ON sync_runs (account_id, completed_at DESC);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id UUID PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES brokerage_accounts(account_id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL,
    currency TEXT NOT NULL,
    equity NUMERIC(38, 12) NOT NULL,
    cash NUMERIC(38, 12) NOT NULL,
    market_value NUMERIC(38, 12) NOT NULL,
    buying_power NUMERIC(38, 12),
    day_profit_loss NUMERIC(38, 12),
    unrealized_profit_loss NUMERIC(38, 12),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE sync_runs
    DROP CONSTRAINT IF EXISTS sync_runs_snapshot_id_fkey;
ALTER TABLE sync_runs
    ADD CONSTRAINT sync_runs_snapshot_id_fkey
    FOREIGN KEY (snapshot_id) REFERENCES portfolio_snapshots(snapshot_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS portfolio_snapshots_account_time_idx
    ON portfolio_snapshots (account_id, captured_at DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_id UUID NOT NULL REFERENCES portfolio_snapshots(snapshot_id) ON DELETE CASCADE,
    external_position_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    currency TEXT NOT NULL,
    quantity NUMERIC(38, 12) NOT NULL,
    last_price NUMERIC(38, 12) NOT NULL,
    market_value NUMERIC(38, 12) NOT NULL,
    average_cost NUMERIC(38, 12),
    cost_basis NUMERIC(38, 12),
    unrealized_profit_loss NUMERIC(38, 12),
    PRIMARY KEY (snapshot_id, external_position_id)
);

CREATE INDEX IF NOT EXISTS position_snapshots_account_symbol_idx
    ON position_snapshots (account_id, symbol);

CREATE TABLE IF NOT EXISTS cash_activities (
    account_id TEXT NOT NULL REFERENCES brokerage_accounts(account_id) ON DELETE CASCADE,
    external_activity_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    amount NUMERIC(38, 12) NOT NULL,
    currency TEXT NOT NULL,
    status TEXT,
    description TEXT,
    is_external_flow BOOLEAN NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_id, external_activity_id)
);

CREATE INDEX IF NOT EXISTS cash_activities_account_time_idx
    ON cash_activities (account_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS order_records (
    account_id TEXT NOT NULL REFERENCES brokerage_accounts(account_id) ON DELETE CASCADE,
    external_order_id TEXT NOT NULL,
    client_order_id TEXT,
    symbol TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    order_type TEXT,
    total_quantity NUMERIC(38, 12) NOT NULL,
    filled_quantity NUMERIC(38, 12) NOT NULL,
    average_fill_price NUMERIC(38, 12),
    placed_at TIMESTAMPTZ,
    filled_at TIMESTAMPTZ,
    commission NUMERIC(38, 12),
    fees NUMERIC(38, 12),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_id, external_order_id)
);

CREATE INDEX IF NOT EXISTS order_records_account_time_idx
    ON order_records (account_id, filled_at DESC, placed_at DESC);

CREATE TABLE IF NOT EXISTS statement_anchors (
    account_id TEXT NOT NULL REFERENCES brokerage_accounts(account_id) ON DELETE CASCADE,
    external_statement_id TEXT NOT NULL,
    statement_date TIMESTAMPTZ NOT NULL,
    ending_equity NUMERIC(38, 12) NOT NULL CHECK (ending_equity >= 0),
    currency TEXT NOT NULL,
    ending_cash NUMERIC(38, 12),
    source_sha256 CHAR(64),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_id, external_statement_id)
);

CREATE INDEX IF NOT EXISTS statement_anchors_account_date_idx
    ON statement_anchors (account_id, statement_date DESC);
