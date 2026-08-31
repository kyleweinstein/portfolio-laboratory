-- Browser cookies contain only an opaque random session identifier. Private
-- Discord OAuth material is encrypted in the internal PostgreSQL service.
CREATE TABLE IF NOT EXISTS discord_viewer_sessions (
    session_id_hash CHAR(64) PRIMARY KEY,
    discord_user_id VARCHAR(32) NOT NULL,
    username VARCHAR(100) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    guild_id VARCHAR(32) NOT NULL,
    encrypted_access_token BYTEA NOT NULL,
    encrypted_refresh_token BYTEA NOT NULL,
    token_expires_at TIMESTAMPTZ NOT NULL,
    membership_checked_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (expires_at > created_at)
);

REVOKE ALL ON discord_viewer_sessions FROM PUBLIC;

CREATE INDEX IF NOT EXISTS discord_viewer_sessions_expiry_idx
    ON discord_viewer_sessions (expires_at);
