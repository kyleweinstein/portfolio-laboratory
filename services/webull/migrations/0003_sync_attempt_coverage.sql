ALTER TABLE sync_runs
    ADD COLUMN IF NOT EXISTS cash_activities_complete BOOLEAN;

ALTER TABLE sync_runs
    ADD COLUMN IF NOT EXISTS message VARCHAR(500);

UPDATE sync_runs
SET message = 'Webull synchronization failed.'
WHERE status = 'ERROR'
  AND message IS NULL
  AND error_message IS NOT NULL;

UPDATE sync_runs
SET error_message = NULL
WHERE error_message IS NOT NULL;

ALTER TABLE brokerage_accounts
    ADD COLUMN IF NOT EXISTS cash_activity_gap_start TIMESTAMPTZ;

ALTER TABLE brokerage_accounts
    ADD COLUMN IF NOT EXISTS cash_activity_gap_end TIMESTAMPTZ;

ALTER TABLE brokerage_accounts
    ADD CONSTRAINT cash_activity_gap_bounds_check CHECK (
        (cash_activity_gap_start IS NULL AND cash_activity_gap_end IS NULL)
        OR (
            cash_activity_gap_start IS NOT NULL
            AND cash_activity_gap_end IS NOT NULL
            AND cash_activity_gap_start <= cash_activity_gap_end
        )
    );

CREATE INDEX IF NOT EXISTS sync_runs_latest_idx
    ON sync_runs (account_id, completed_at DESC, sync_run_id DESC);
