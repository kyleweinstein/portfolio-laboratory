-- A successful activity request proves only the requested interval, not all
-- account history. Existing accounts intentionally remain NULL/unknown until
-- a fresh sync or backfill records an explicit continuous range.
ALTER TABLE brokerage_accounts
    ADD COLUMN IF NOT EXISTS cash_activity_coverage_start TIMESTAMPTZ;

ALTER TABLE brokerage_accounts
    ADD COLUMN IF NOT EXISTS cash_activity_coverage_end TIMESTAMPTZ;

ALTER TABLE brokerage_accounts
    ADD CONSTRAINT cash_activity_coverage_bounds_check CHECK (
        (
            cash_activity_coverage_start IS NULL
            AND cash_activity_coverage_end IS NULL
        )
        OR (
            cash_activity_coverage_start IS NOT NULL
            AND cash_activity_coverage_end IS NOT NULL
            AND cash_activity_coverage_start <= cash_activity_coverage_end
        )
    );

CREATE INDEX IF NOT EXISTS brokerage_accounts_cash_activity_coverage_idx
    ON brokerage_accounts (
        account_id,
        cash_activity_coverage_start,
        cash_activity_coverage_end
    );
