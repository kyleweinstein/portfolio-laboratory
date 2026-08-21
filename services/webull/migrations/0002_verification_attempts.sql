CREATE TABLE IF NOT EXISTS verification_attempts (
    attempt_id UUID PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'webull' CHECK (provider = 'webull'),
    state TEXT NOT NULL CHECK (
        state IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT')
    ),
    stage TEXT NOT NULL CHECK (
        stage IN (
            'STARTING',
            'VERIFYING_ACCESS',
            'DISCOVERING_ACCOUNTS',
            'SYNCING_ACCOUNT',
            'FINALIZING',
            'COMPLETE'
        )
    ),
    started_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    error_code VARCHAR(64),
    error_message VARCHAR(500)
);

CREATE UNIQUE INDEX IF NOT EXISTS verification_attempts_one_running_idx
    ON verification_attempts (provider)
    WHERE state = 'RUNNING';

CREATE INDEX IF NOT EXISTS verification_attempts_latest_idx
    ON verification_attempts (provider, started_at DESC, attempt_id DESC);
